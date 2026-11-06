"""Pencarian Rute Aman Menghindari Gangguan Jalan di Makassar (A*, UCS, BFS).

Jalankan GUI      : python main.py
Jalankan evaluasi : python main.py --eval

Data dibaca langsung dari berkas .xlsx di folder data/ (sheet Parameters, Nodes,
Edges, Hazard Locations, Test Scenarios). Tanpa .xlsx, program membaca CSV dengan
nama sheet yang sama (nodes.csv, edges.csv, ...).
Peta digambar sebagai skema jaringan jalan dari kolom x_km, y_km pada sheet Nodes.
Latar peta (opsional): data/peta.png + kolom x_px, y_px pada sheet Nodes (posisi piksel
simpul di gambar, minimal 2 simpul). Butuh Pillow.
--eval juga menyimpan hasil ke hasil_evaluasi.xlsx.
"""
import csv
import heapq
import math
import os
import random
import re
import sys
import time
from collections import deque

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

GAMMA = 2.0            # dibaca dari sheet Parameters bila ada
PENALTY = {"tidak ada": 0, "ringan": 1, "berat": 2}
CLOSED = "ditutup"
KINDS = ["demonstrasi", "banjir", "kecelakaan"]
LOCATION_NAMES = {
    "UC": "Universitas Ciputra Makassar",
    "MP": "Mall Panakkukang",
    "Mari": "Mall Ratu Indah",
    "TSM": "Trans Studio Mall",
}
TIMING_RUNS = 100
MIN_MAP, MIN_PANEL, MIN_H = 420, 460, 560     # ukuran minimum area peta, panel, dan tinggi jendela (px)


# ----------------------------------------------------------------- data
def num(s):
    s = str(s).strip()
    if s.upper() == "INF":
        return math.inf
    return float(s.replace(",", "."))


def opt_num(s):
    """Angka, atau None bila sel kosong / bukan angka (untuk kolom opsional)."""
    try:
        return num(s) if str(s).strip() else None
    except ValueError:
        return None


_WB = {}


def find_workbook():
    if os.path.isdir(DATA):
        for f in sorted(os.listdir(DATA)):
            if f.lower().endswith(".xlsx") and not f.startswith("~"):
                return os.path.join(DATA, f)
    return None


def read_table(name):
    """Baca satu sheet (baris pertama = header) sebagai list of dict berisi string."""
    path = find_workbook()
    if path:
        try:
            from openpyxl import load_workbook
            if path not in _WB:
                _WB[path] = load_workbook(path, data_only=True)
            for ws in _WB[path].worksheets:
                if ws.title.strip().lower() == name.lower():
                    rows = list(ws.iter_rows(values_only=True))
                    head = [str(h).strip() if h is not None else "" for h in rows[0]]
                    return [dict(zip(head, ["" if v is None else str(v).strip() for v in r]))
                            for r in rows[1:] if sum(v is not None for v in r) >= 2]
            return []
        except ImportError:
            pass
    csv_path = os.path.join(DATA, name.lower().replace(" ", "_") + ".csv")
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    # sama seperti pembacaan .xlsx: baris kosong / baris catatan (< 2 sel terisi) dilewati
    return [r for r in rows if sum(bool(v) for v in r.values()) >= 2]


def get(row, prefix, default=""):
    """Ambil nilai kolom yang namanya diawali prefix (mis. 'x_km' untuk 'x_km (east)')."""
    for k, v in row.items():
        if k.lower().startswith(prefix):
            return v
    return default


class Graph:
    def __init__(self, nodes, edges):
        self.nodes, self.edges = nodes, edges
        self.view = None            # diisi layout(): ukuran kanvas, skala, dan offset gambar
        self.adj = {n: [] for n in nodes}
        for e in edges.values():
            self.adj[e["a"]].append((e["b"], e["id"]))
            self.adj[e["b"]].append((e["a"], e["id"]))
        for n in self.adj:          # urutan tetangga tetap (sesuai id) agar hasil BFS bisa diulang
            self.adj[n].sort()


def load_graph():
    global GAMMA
    for r in read_table("Parameters"):
        if r.get("Parameter", "").lower().startswith("gamma"):
            GAMMA = num(r["Value"])
    nodes, edges = {}, {}
    for r in read_table("Nodes"):
        nodes[r["node_id"]] = dict(
            id=r["node_id"], name=r["name"],
            kind="location" if r["type"].lower().startswith("start") else "junction",
            kx=num(get(r, "x_km")), ky=num(get(r, "y_km")),
            px=opt_num(get(r, "x_px")), py=opt_num(get(r, "y_px")),
            can_start=r.get("role", "").lower().startswith("start"))
    for r in read_table("Edges"):
        edges[r["edge_id"]] = dict(id=r["edge_id"], a=r["from"], b=r["to"],
                                   road=r["road_name"], dist=num(r["distance_km"]))
    if not nodes or not edges:
        sys.exit("Sheet Nodes / Edges tidak ditemukan. Taruh .xlsx dataset di folder data/.")
    for e in edges.values():
        if e["a"] not in nodes or e["b"] not in nodes:
            sys.exit(f"Ruas {e['id']} memakai simpul yang tidak ada di sheet Nodes.")
    return Graph(nodes, edges)


def straight_km(g, a, b):
    na, nb = g.nodes[a], g.nodes[b]
    return math.hypot(na["kx"] - nb["kx"], na["ky"] - nb["ky"])


def check_heuristic(g):
    """Heuristik admissible bila garis lurus <= jarak jalan pada setiap ruas."""
    return [e["id"] for e in g.edges.values()
            if straight_km(g, e["a"], e["b"]) > e["dist"] + 1e-9]


MAP_FILES = ("peta.png", "map.png", "peta.jpg", "map.jpg")


def fit_axis(pairs):
    """Regresi linear px = a * km + b dari pasangan (km, px)."""
    n = len(pairs)
    mean_k = sum(k for k, _ in pairs) / n
    mean_p = sum(p for _, p in pairs) / n
    var = sum((k - mean_k) ** 2 for k, _ in pairs)
    if var < 1e-12:
        return None
    a = sum((k - mean_k) * (p - mean_p) for k, p in pairs) / var
    return a, mean_p - a * mean_k


def load_background(g):
    """Latar peta opsional: data/peta.png + kolom x_px, y_px (posisi piksel simpul di gambar).

    Minimal 2 simpul (beda posisi kiri-kanan dan atas-bawah) harus diisi x_px, y_px.
    Hasil: (dict latar atau None, pesan)."""
    path = next((os.path.join(DATA, f) for f in MAP_FILES if os.path.exists(os.path.join(DATA, f))), None)
    if not path:
        return None, ""
    name = os.path.basename(path)
    pts = [n for n in g.nodes.values() if n["px"] is not None and n["py"] is not None]
    fx = fit_axis([(n["kx"], n["px"]) for n in pts]) if len(pts) >= 2 else None
    fy = fit_axis([(n["ky"], n["py"]) for n in pts]) if len(pts) >= 2 else None
    if not fx or not fy:
        return None, (f"Latar peta {name} tidak dipakai: isi kolom x_px dan y_px pada sheet Nodes "
                      f"untuk minimal 2 simpul yang posisinya berbeda.")
    if fx[0] <= 0 or fy[0] >= 0:
        return None, f"Latar peta {name} tidak dipakai: x_px/y_px tidak cocok dengan arah x_km (timur)/y_km (utara)."
    try:
        from PIL import Image
    except ImportError:
        return None, f"Latar peta {name} tidak dipakai: pasang Pillow (pip install pillow)."
    try:
        img = Image.open(path).convert("RGBA")
    except Exception as exc:                  # berkas rusak / bukan gambar: jalan tanpa latar
        return None, f"Latar peta {name} tidak dipakai: gambar tidak bisa dibuka ({exc.__class__.__name__})."
    # sedikit dipudarkan supaya garis jalan dan rute tetap menonjol di atas peta
    img = Image.blend(img, Image.new("RGBA", img.size, (255, 255, 255, 255)), 0.35)
    err = max(max(abs(fx[0] * n["kx"] + fx[1] - n["px"]), abs(fy[0] * n["ky"] + fy[1] - n["py"])) for n in pts)
    return (dict(image=img, ax=fx[0], bx=fx[1], ay=fy[0], by=fy[1], name=name, error_px=err),
            f"Latar peta: {name}, dikalibrasi dari {len(pts)} simpul (selisih terbesar {err:.0f} px).")


def hazard_defaults():
    """Sheet Hazard Locations -> {edge_id: (jenis, tingkat bawaan)}."""
    out = {}
    for r in read_table("Hazard Locations"):
        lv = r.get("default level", "").lower()
        level = CLOSED if lv.startswith("ditutup") else "berat" if lv.startswith("berat") else None
        if level:
            for e in re.findall(r"E\d+", r.get("graph edges", "")):
                out[e] = (r["hazard"].lower().split()[0], level)
    return out


def load_scenarios():
    """Sheet Test Scenarios -> list skenario dengan gangguan {edge: (jenis, tingkat)}."""
    out, defaults = [], None
    for r in read_table("Test Scenarios"):
        edges = re.findall(r"E\d+", r.get("hazard_edges", ""))
        level, htype = r.get("level", "-").lower(), r.get("hazard_type", "-").lower()
        dis = {}
        if edges and "+" in level + htype:      # gabungan: tingkat per ruas dari Hazard Locations
            defaults = defaults or hazard_defaults()
            dis = {e: defaults.get(e, ("demonstrasi", CLOSED)) for e in edges}
        elif edges:
            dis = {e: (htype.split()[0], CLOSED if level.startswith("ditutup") else level)
                   for e in edges}
        out.append(dict(id=r["ID"], name=r["scenario"], start=r["start"], goal=r["goal"],
                        dis=dis, expected=r.get("cost with hazard", ""),
                        expected_changed=r.get("route changed?", "")))
    return out


# --------------------------------------------------------------- search
def edge_cost(g, eid, dis):
    """cost(e) = d(e) + gamma * p(e); ruas ditutup -> None (dihapus dari graf)."""
    level = dis.get(eid, (None, "tidak ada"))[1]
    if level == CLOSED:
        return None
    return g.edges[eid]["dist"] + GAMMA * PENALTY[level]


def finish(g, parent, goal, dis, expanded):
    nodes, eids, n = [goal], [], goal
    while n in parent:
        n, eid = parent[n]
        nodes.append(n)
        eids.append(eid)
    nodes.reverse()
    eids.reverse()
    return dict(found=True, nodes=nodes, edges=eids, expanded=expanded,
                dist=sum(g.edges[i]["dist"] for i in eids),
                cost=sum(edge_cost(g, i, dis) or 0.0 for i in eids))


def not_found(expanded):
    return dict(found=False, nodes=[], edges=[], expanded=expanded, dist=math.inf, cost=math.inf)


def best_first(g, start, goal, dis, use_h):
    """A* bila use_h=True (f = g + h), UCS/Dijkstra bila False (f = g)."""
    def h(n):
        return straight_km(g, n, goal) if use_h else 0.0

    tie = 0
    frontier = [(h(start), 0, start)]
    best, parent, done, expanded = {start: 0.0}, {}, set(), 0
    while frontier:
        _, _, n = heapq.heappop(frontier)
        if n in done:
            continue
        done.add(n)
        expanded += 1
        if n == goal:
            return finish(g, parent, goal, dis, expanded)
        for m, eid in g.adj[n]:
            c = edge_cost(g, eid, dis)
            if c is None:
                continue
            ng = best[n] + c
            if ng < best.get(m, math.inf):
                best[m] = ng
                parent[m] = (n, eid)
                tie += 1
                heapq.heappush(frontier, (ng + h(m), tie, m))
    return not_found(expanded)


def bfs(g, start, goal, dis):
    """Rute dengan jumlah ruas paling sedikit, bobot diabaikan (ruas ditutup tetap dihindari)."""
    queue, seen, parent, expanded = deque([start]), {start}, {}, 0
    while queue:
        n = queue.popleft()
        expanded += 1
        if n == goal:
            return finish(g, parent, goal, dis, expanded)
        for m, eid in g.adj[n]:
            if m in seen or edge_cost(g, eid, dis) is None:
                continue
            seen.add(m)
            parent[m] = (n, eid)
            queue.append(m)
    return not_found(expanded)


ALGOS = {
    "A*": lambda g, s, t, d: best_first(g, s, t, d, True),
    "UCS": lambda g, s, t, d: best_first(g, s, t, d, False),
    "BFS": bfs,
}


def run_algo(name, g, s, t, dis, runs=TIMING_RUNS):
    fn = ALGOS[name]
    t0 = time.perf_counter()
    for _ in range(runs):
        res = fn(g, s, t, dis)
    res["ms"] = (time.perf_counter() - t0) * 1000 / runs
    return res


def dropdown_label(node):
    """Label dropdown 'ID - nama'; lokasi yang tidak ada di LOCATION_NAMES memakai nama dari sheet Nodes."""
    return f"{node['id']} - {LOCATION_NAMES.get(node['id'], node['name'])}"


def comma(value):
    """Angka desimal berkoma untuk tampilan GUI, misalnya 1.4 -> '1,40'."""
    text = value if isinstance(value, str) else f"{value:.2f}"
    return text.replace(".", ",")


def fc(x):
    return "INF" if x == math.inf else f"{x:.2f}"


# ----------------------------------------------------------- evaluation
RESULT_FILE = os.path.join(BASE, "hasil_evaluasi.xlsx")   # di luar data/ agar tidak terbaca sebagai dataset


def same_cost(x, y):
    return x == y or abs(x - y) < 1e-9


def passes(res, dis, closed):
    """Ruas pada rute yang ditutup (closed=True) atau berpenalti ringan/berat (closed=False)."""
    return [e for e in res["edges"] if e in dis and (dis[e][1] == CLOSED) == closed]


def route_change(g, s, t, dis, res, algo="A*"):
    """(berubah?, responsnya tepat?) dibanding rute A* tanpa gangguan.

    Tepat = rute hanya berubah bila ada gangguan pada rute terbaik semula, dan rute
    pasti berubah bila salah satu ruas rute terbaik semula ditutup."""
    base = ALGOS[algo](g, s, t, {})
    changed = not res["found"] or res["nodes"] != base["nodes"]
    hit = [e for e in base["edges"] if e in dis]
    must_change = any(dis[e][1] == CLOSED for e in hit)
    return changed, (not changed or bool(hit)) and (changed or not must_change)


def change_text(res, changed):
    return "Tidak ada rute" if not res["found"] else "Ya" if changed else "Tidak"


def evaluate(g):
    scenarios = load_scenarios()
    if not scenarios:
        print("Sheet Test Scenarios kosong atau tidak ada.")
        return
    print(f"{'ID':<3}{'Skenario':<27}{'Harap':>6}{'UCS':>7}{'A*':>7}{'BFS':>7}{'Opt':>4}"
          f"{'Berubah':>16}{'Resp':>5}{'Tutup':>6}{'Penalti B/U/A':>14}{'Eks B/U/A':>11}{'ms A*':>7}")
    rows, summary = [], []
    n_opt = n_closed_ok = n_exp = n_exp_ok = bfs_opt = n_resp = n_with_route = 0
    pen_ids = {a: [] for a in ALGOS}
    exp_sum = dict.fromkeys(ALGOS, 0)
    ms_sum = dict.fromkeys(ALGOS, 0.0)
    for sc in scenarios:
        s, t, dis = sc["start"], sc["goal"], sc["dis"]
        res = {a: run_algo(a, g, s, t, dis) for a in ALGOS}
        a_star, ucs = res["A*"], res["UCS"]
        has_route = a_star["found"]
        opt = same_cost(a_star["cost"], ucs["cost"])
        changed, resp = route_change(g, s, t, dis, a_star)
        if sc["expected_changed"]:            # bandingkan juga dengan kolom 'route changed?'
            resp = resp and sc["expected_changed"].lower().startswith("y") == changed
        no_closed = not passes(a_star, dis, True)
        pen = {a: passes(res[a], dis, False) for a in ALGOS}
        mark = "-"
        if sc["expected"]:
            ok = abs(num(sc["expected"]) - ucs["cost"]) < 0.011 or same_cost(num(sc["expected"]), ucs["cost"])
            mark = "OK" if ok else "X"
            n_exp += 1
            n_exp_ok += ok
        n_closed_ok += no_closed
        n_resp += resp
        n_with_route += has_route
        if has_route:
            n_opt += opt
            bfs_opt += same_cost(res["BFS"]["cost"], ucs["cost"])
        for a in ALGOS:
            if pen[a]:
                pen_ids[a].append(sc["id"])
            if has_route:
                exp_sum[a] += res[a]["expanded"]
            ms_sum[a] += res[a]["ms"]
            rows.append([sc["id"], sc["name"], s, t, a, " > ".join(res[a]["nodes"]) or "Tidak ada rute",
                         res[a]["dist"], res[a]["cost"], res[a]["expanded"], round(res[a]["ms"], 4),
                         "Ya" if same_cost(res[a]["cost"], ucs["cost"]) else "Tidak",
                         ", ".join(passes(res[a], dis, True)) or "-", ", ".join(pen[a]) or "-",
                         change_text(res[a], route_change(g, s, t, dis, res[a], a)[0])])
        yn = lambda b: "Y" if b else "N"
        print(f"{sc['id']:<3}{sc['name'][:25]:<27}{mark:>6}{fc(ucs['cost']):>7}{fc(a_star['cost']):>7}"
              f"{fc(res['BFS']['cost']):>7}{yn(opt):>4}{change_text(a_star, changed):>16}"
              f"{'OK' if resp else 'X':>5}{'OK' if no_closed else 'X':>6}"
              f"{yn(pen['BFS'][:1]) + '/' + yn(pen['UCS'][:1]) + '/' + yn(pen['A*'][:1]):>14}"
              f"{str(res['BFS']['expanded']) + '/' + str(ucs['expanded']) + '/' + str(a_star['expanded']):>11}"
              f"{a_star['ms']:>7.3f}")
    n = len(scenarios)
    summary += [
        ("Optimalitas A* (biaya A* = UCS)", f"{n_opt}/{n_with_route} skenario yang punya rute"),
        ("BFS optimal", f"{bfs_opt}/{n_with_route} skenario yang punya rute"),
        ("Keamanan: rute A* tidak melewati ruas ditutup", f"{n_closed_ok}/{n}"),
    ]
    for a in ALGOS:
        ids = ", ".join(pen_ids[a]) or "-"
        summary.append((f"Keamanan: rute {a} melewati ruas berpenalti", f"{len(pen_ids[a])}/{n} (skenario {ids})"))
    summary.append(("Ketepatan respons A*", f"{n_resp}/{n} (rute berubah hanya bila gangguan ada di rute "
                                            f"terbaik; cocok dengan kolom 'route changed?')"))
    if n_exp:
        summary.append(("Cocok dengan biaya di sheet Test Scenarios", f"{n_exp_ok}/{n_exp}"))
    for a in ALGOS:
        summary.append((f"{a} rata-rata simpul diekspansi", f"{exp_sum[a] / max(n_with_route, 1):.1f} "
                                                           f"({n_with_route} skenario yang punya rute)"))
    for a in ALGOS:
        summary.append((f"{a} rata-rata waktu", f"{ms_sum[a] / n:.3f} ms ({TIMING_RUNS}x per skenario)"))
    print()
    for k, v in summary:
        print(f"{k:<46}: {v}")
    sim = simulate_accidents(g)
    check = route_check(g)
    save_results(rows, summary, sim, check)


def simulate_accidents(g, n=10, seed=42):
    """Kecelakaan acak (ruas mana pun, ringan/berat) pada rute UC -> MP, seed tetap."""
    s, t = "UC", "MP"
    if s not in g.nodes or t not in g.nodes:
        return []
    rnd = random.Random(seed)
    edge_ids = list(g.edges)
    base = ALGOS["A*"](g, s, t, {})
    print(f"\nSimulasi kecelakaan acak UC -> MP (seed {seed}), biaya dasar {fc(base['cost'])}:")
    ok = n_resp = 0
    rows = []
    for k in range(1, n + 1):
        eid, level = rnd.choice(edge_ids), rnd.choice(["ringan", "berat"])
        dis = {eid: ("kecelakaan", level)}
        a, u = ALGOS["A*"](g, s, t, dis), ALGOS["UCS"](g, s, t, dis)
        same = same_cost(a["cost"], u["cost"])
        changed, resp = route_change(g, s, t, dis, a)
        ok += same
        n_resp += resp
        on_base = "di rute" if eid in base["edges"] else "di luar rute"
        rows.append([k, eid, level, on_base, a["cost"], u["cost"], "Ya" if changed else "Tidak",
                     "OK" if same else "GAGAL", "OK" if resp else "X"])
        print(f"  {k:>2}. {eid} {level:<7} {on_base:<13} A*={fc(a['cost'])} UCS={fc(u['cost'])} "
              f"{'berubah' if changed else 'tetap':<8}{'OK' if same else 'GAGAL':<6}"
              f"respons {'OK' if resp else 'X'}")
    print(f"  A* = UCS pada {ok}/{n} simulasi, respons tepat pada {n_resp}/{n} simulasi")
    return rows


def route_check(g):
    """Sheet Route Check: jumlah jarak ruas (+ detour) dibanding total jarak rute Google Maps."""
    rows, out = [], []
    for r in read_table("Route Check"):
        try:
            google = num(r.get("google_km", ""))
        except ValueError:
            continue
        cols = sorted((k for k in r if k.lower().startswith("edge_")), key=lambda k: int(k.split("_")[1]))
        eids = [r[k] for k in cols if r[k]]
        path = [p.strip() for p in r.get("node_path", "").split(">") if p.strip()]
        missing = [e for e in eids if e not in g.edges]
        if missing:
            print(f"  {r['route']}: ruas {', '.join(missing)} tidak ada di graf")
            continue
        connected = len(eids) == len(path) - 1 and all(
            {g.edges[e]["a"], g.edges[e]["b"]} == {path[i], path[i + 1]} for i, e in enumerate(eids))
        detour = num(r.get("detour_km") or 0)
        graph_km = sum(g.edges[e]["dist"] for e in eids) + detour
        rows.append((r["route"], google, graph_km, round(graph_km - google, 6) + 0.0, connected))
    if not rows:
        return out
    print(f"\nValidasi jarak graf terhadap Google Maps ({len(rows)} rute acuan):")
    for name, google, graph_km, diff, connected in rows:
        print(f"  {name[:42]:<44}Google {google:5.2f} km  graf {graph_km:5.2f} km  "
              f"selisih {diff:+.2f}{'' if connected else '  (urutan ruas tidak sesuai node_path!)'}")
        out.append([name, google, round(graph_km, 2), round(diff, 2), "OK" if connected else "X"])
    worst = max(abs(d) for _, _, _, d, _ in rows)
    print(f"  Selisih terbesar: {worst:.2f} km; jalur ruas sesuai node_path: "
          f"{sum(c for *_, c in rows)}/{len(rows)}")
    return out


def save_results(rows, summary, sim, check):
    """Simpan hasil evaluasi ke hasil_evaluasi.xlsx (atau .csv bila openpyxl tidak ada)."""
    head = ["ID", "Skenario", "Awal", "Tujuan", "Algoritma", "Rute", "Jarak (km)", "Biaya",
            "Simpul diekspansi", "Waktu (ms)", "Optimal", "Lewat ruas ditutup", "Lewat ruas berpenalti",
            "Rute berubah"]
    inf = lambda v: "INF" if isinstance(v, float) and v == math.inf else v
    try:
        from openpyxl import Workbook
    except ImportError:
        path = os.path.splitext(RESULT_FILE)[0] + ".csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(head)
            w.writerows([[inf(v) for v in r] for r in rows])
        print(f"\nHasil per skenario disimpan ke {path}")
        return
    wb = Workbook()
    sheets = [("Skenario", head, rows),
              ("Ringkasan", ["Metrik", "Hasil"], summary),
              ("Simulasi Kecelakaan", ["Run", "Ruas", "Tingkat", "Posisi", "Biaya A*", "Biaya UCS",
                                       "Rute berubah", "A* = UCS", "Respons"], sim),
              ("Cek Google Maps", ["Rute", "Google (km)", "Graf (km)", "Selisih (km)", "Jalur sesuai"], check)]
    for i, (title, header, data) in enumerate(sheets):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        ws.append(header)
        for r in data:
            ws.append([inf(v) for v in r])
    try:
        wb.save(RESULT_FILE)
        print(f"\nHasil evaluasi disimpan ke {RESULT_FILE}")
    except PermissionError:
        print(f"\nTidak bisa menyimpan {RESULT_FILE} (berkas sedang dibuka di Excel?)")


# ------------------------------------------------------------------ GUI
ROAD_COLOR = "#9e9e9e"
ROUTE_COLOR = "#1565c0"
LEVEL_COLOR = {"ringan": "#f57c00", "berat": "#c62828", "ditutup": "#37474f"}
LABEL_TEXT_COLOR = "#111111"
LABEL_PAD_X, LABEL_PAD_Y = 4, 1


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def boxes_overlap(a, b):
    """Kotak (x1, y1, x2, y2) saling tumpang tindih?"""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def segment_hits_box(x1, y1, x2, y2, box):
    """Apakah garis (x1, y1)-(x2, y2) memotong kotak? (Liang-Barsky)"""
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - box[0]), (dx, box[2] - x1), (-dy, y1 - box[1]), (dy, box[3] - y1)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def box_inside(box, W, H):
    return box[0] >= 0 and box[1] >= 0 and box[2] <= W and box[3] <= H


def clamp_box(box, W, H):
    """Geser kotak seperlunya supaya seluruhnya berada di dalam kanvas W x H."""
    dx = max(0 - box[0], 0) - max(box[2] - W, 0)
    dy = max(0 - box[1], 0) - max(box[3] - H, 0)
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def layout(g, W, H, margin=60):
    """Posisi gambar tiap simpul: skema dari x_km/y_km di tengah kanvas W x H.

    Satu skala untuk x dan y (bentuk jalan tidak gepeng); sisa ruang dibagi rata
    ke dua sisi, jadi margin kiri = kanan dan atas = bawah."""
    margin = min(margin, W // 8, H // 8)    # inset aman: tepi tidak memakan area kecil
    xs, ys = [n["kx"] for n in g.nodes.values()], [n["ky"] for n in g.nodes.values()]
    width_km, height_km = max(xs) - min(xs), max(ys) - min(ys)
    scale = max(1e-6, min((W - 2 * margin) / (width_km or 1), (H - 2 * margin) / (height_km or 1)))
    left = (W - width_km * scale) / 2
    top = (H - height_km * scale) / 2
    for n in g.nodes.values():
        n["sx"] = left + (n["kx"] - min(xs)) * scale
        n["sy"] = top + (max(ys) - n["ky"]) * scale
    # dipakai untuk menjaga label tetap di kanvas dan untuk memetakan latar peta
    g.view = dict(W=W, H=H, left=left, top=top, scale=scale, min_x=min(xs), max_y=max(ys))


def run_gui(g, heuristic_note, bg=None):
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import ttk

    root = tk.Tk()
    root.title("Rute Aman Makassar - A*")
    dis, state = {}, {"results": {}, "route_segs": []}
    show_bg = tk.BooleanVar(value=True)

    layout(g, MIN_MAP, MIN_H)
    canvas = tk.Canvas(root, width=MIN_MAP, height=MIN_H, bg="white")
    canvas.grid(row=0, column=0, sticky="nsew")
    panel = ttk.Frame(root, padding=8)
    panel.grid(row=0, column=1, sticky="nsew")
    # peta 50 : 50 panel, dengan lebar minimum agar tidak ada yang terpotong
    root.columnconfigure(0, weight=1, minsize=MIN_MAP, uniform="half")
    root.columnconfigure(1, weight=1, minsize=MIN_PANEL, uniform="half")
    root.rowconfigure(0, weight=1, minsize=MIN_H)
    root.minsize(MIN_MAP + MIN_PANEL, MIN_H)
    root.geometry(f"{2 * MIN_PANEL + 160}x{MIN_H + 140}")

    label_to_id = {dropdown_label(n): i for i, n in g.nodes.items()}
    starts = [dropdown_label(n) for n in g.nodes.values() if n["can_start"]]
    all_goals = [dropdown_label(n) for n in g.nodes.values() if n["kind"] == "location"]
    start_var, goal_var, algo_var = tk.StringVar(), tk.StringVar(), tk.StringVar(value="A*")

    ttk.Label(panel, text="Titik awal").pack(anchor="w")
    cb_s = ttk.Combobox(panel, textvariable=start_var, values=starts, state="readonly", width=34)
    cb_s.pack(fill="x")
    ttk.Label(panel, text="Tujuan").pack(anchor="w", pady=(6, 0))
    cb_g = ttk.Combobox(panel, textvariable=goal_var, values=all_goals, state="readonly", width=34)
    cb_g.pack(fill="x")
    ttk.Label(panel, text="Rute yang digambar").pack(anchor="w", pady=(6, 0))
    row = ttk.Frame(panel)
    row.pack(anchor="w")
    for a in ALGOS:
        ttk.Radiobutton(row, text=a, value=a, variable=algo_var,
                        command=lambda: refresh()).pack(side="left", padx=(0, 8))
    ttk.Button(panel, text="Hapus semua gangguan",
               command=lambda: (dis.clear(), search())).pack(anchor="w", pady=6)
    if bg:
        ttk.Checkbutton(panel, text="Tampilkan latar peta", variable=show_bg,
                        command=lambda: draw()).pack(anchor="w")
    hint = ttk.Label(panel, text="Klik garis jalan pada skema untuk menandai gangguan.", wraplength=320)
    hint.pack(anchor="w", fill="x")
    panel.bind("<Configure>", lambda ev: hint.config(wraplength=max(200, ev.width - 24)))
    text = tk.Text(panel, width=46, height=30, font=("Courier", 9), state="disabled", wrap="word")
    text.pack(pady=6, fill="both", expand=True)

    def show(msg):
        text.config(state="normal")
        text.delete("1.0", "end")
        text.insert("end", msg)
        text.config(state="disabled")

    location_font = tkfont.Font(family="Helvetica", size=10, weight="bold")
    hazard_font = tkfont.Font(family="Helvetica", size=9, weight="bold")

    def node_radius(node):
        return 9 if node["kind"] == "location" else 5

    def label_box(center_x, center_y, text, font):
        half_w = font.measure(text) / 2 + LABEL_PAD_X
        half_h = font.metrics("linespace") / 2 + LABEL_PAD_Y
        return (center_x - half_w, center_y - half_h, center_x + half_w, center_y + half_h)

    def draw_label(box, text, font, outline):
        """Teks gelap di atas kotak putih, supaya terbaca di atas garis apa pun."""
        canvas.create_rectangle(box, fill="white", outline=outline)
        canvas.create_text((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, text=text,
                           font=font, fill=LABEL_TEXT_COLOR)

    def best_box(centers, text, font, obstacles, anchor, skip_edge=None):
        """Pilih posisi label dengan skor terkecil. Semua kandidat digeser masuk ke kanvas.
        Skor: menimpa simpul/label lain (paling dihindari) > menutupi garis rute > jarak
        dari titik acuan (label sebisa mungkin dekat ruas/simpulnya)."""
        W, H = g.view["W"], g.view["H"]
        best = None
        for cx, cy in centers:
            box = clamp_box(label_box(cx, cy, text, font), W, H)
            hits = sum(boxes_overlap(box, other) for other in obstacles)
            cross = sum(segment_hits_box(*seg, box) for eid, seg in state["route_segs"] if eid != skip_edge)
            dist = math.hypot((box[0] + box[2]) / 2 - anchor[0], (box[1] + box[3]) / 2 - anchor[1])
            score = hits * 1000 + cross * 150 + dist
            if best is None or score < best[0]:
                best = (score, box)
        return best[1]

    def location_label_box(node, text, obstacles):
        """Label lokasi: coba di atas, bawah, kanan, lalu kiri lingkaran."""
        radius, gap = node_radius(node), 4
        half_w = location_font.measure(text) / 2 + LABEL_PAD_X
        half_h = location_font.metrics("linespace") / 2 + LABEL_PAD_Y
        centers = [
            (node["sx"], node["sy"] - radius - gap - half_h),
            (node["sx"], node["sy"] + radius + gap + half_h),
            (node["sx"] + radius + gap + half_w, node["sy"]),
            (node["sx"] - radius - gap - half_w, node["sy"]),
        ]
        # jarak ke pusat simpul sama untuk keempat posisi; urutan di atas jadi pemecah seri
        return best_box(centers, text, location_font, obstacles, (node["sx"], node["sy"]))

    def edge_mid(edge):
        a, b = g.nodes[edge["a"]], g.nodes[edge["b"]]
        return (a["sx"] + b["sx"]) / 2, (a["sy"] + b["sy"]) / 2

    def hazard_label_box(edge, text, obstacles):
        """Label gangguan: di tengah ruas, atau digeser ke tempat kosong terdekat."""
        mid_x, mid_y = edge_mid(edge)
        step_x = hazard_font.measure(text) / 4
        step_y = hazard_font.metrics("linespace") + 4
        centers = [(mid_x + col * step_x, mid_y + row * step_y)
                   for row in range(-6, 7) for col in range(-8, 9)]
        return best_box(centers, text, hazard_font, obstacles, (mid_x, mid_y), skip_edge=edge["id"])

    def draw_background():
        """Potong bagian peta yang terlihat di kanvas lalu ubah ukurannya ke ukuran kanvas."""
        if not bg or not show_bg.get():
            return
        v = g.view
        key = (v["W"], v["H"], v["scale"])
        if state.get("bg_key") != key:
            from PIL import Image, ImageTk
            km_left = v["min_x"] - v["left"] / v["scale"]
            km_right = v["min_x"] + (v["W"] - v["left"]) / v["scale"]
            km_top = v["max_y"] + v["top"] / v["scale"]
            km_bottom = v["max_y"] - (v["H"] - v["top"]) / v["scale"]
            box = (bg["ax"] * km_left + bg["bx"], bg["ay"] * km_top + bg["by"],
                   bg["ax"] * km_right + bg["bx"], bg["ay"] * km_bottom + bg["by"])
            part = bg["image"].transform((int(v["W"]), int(v["H"])), Image.Transform.EXTENT, box,
                                         Image.Resampling.BILINEAR)
            state["bg_photo"], state["bg_key"] = ImageTk.PhotoImage(part), key
        canvas.create_image(0, 0, image=state["bg_photo"], anchor="nw")

    def draw():
        canvas.delete("all")
        draw_background()
        shown = state["results"].get(algo_var.get())
        on_route = set(shown["edges"]) if shown and shown["found"] else set()
        state["route_segs"] = [(eid, (g.nodes[g.edges[eid]["a"]]["sx"], g.nodes[g.edges[eid]["a"]]["sy"],
                                      g.nodes[g.edges[eid]["b"]]["sx"], g.nodes[g.edges[eid]["b"]]["sy"]))
                               for eid in on_route]
        for e in g.edges.values():
            a, b = g.nodes[e["a"]], g.nodes[e["b"]]
            canvas.create_line(a["sx"], a["sy"], b["sx"], b["sy"], fill=ROAD_COLOR, width=2)
        for eid in on_route:
            e = g.edges[eid]
            a, b = g.nodes[e["a"]], g.nodes[e["b"]]
            canvas.create_line(a["sx"], a["sy"], b["sx"], b["sy"], fill=ROUTE_COLOR, width=6)
        for eid, (kind, level) in dis.items():
            if eid in on_route:
                continue
            e = g.edges[eid]
            a, b = g.nodes[e["a"]], g.nodes[e["b"]]
            canvas.create_line(a["sx"], a["sy"], b["sx"], b["sy"], fill=LEVEL_COLOR[level],
                               width=5 if level == CLOSED else 4, dash=(6, 4) if level == CLOSED else ())
        s, t = label_to_id.get(start_var.get()), label_to_id.get(goal_var.get())
        obstacles = []
        for i, n in g.nodes.items():
            r = node_radius(n)
            fill = "#2e7d32" if i == s else "#6a1b9a" if i == t else "white"
            canvas.create_oval(n["sx"] - r, n["sy"] - r, n["sx"] + r, n["sy"] + r,
                               fill=fill, outline="black", width=2)
            obstacles.append((n["sx"] - r - 2, n["sy"] - r - 2, n["sx"] + r + 2, n["sy"] + r + 2))
        # posisi label lokasi dulu (melekat ke simpulnya), lalu label gangguan di sisa ruang
        labels = []
        for n in g.nodes.values():
            if n["kind"] == "location":
                box = location_label_box(n, n["id"], obstacles)
                labels.append((box, n["id"], location_font, "#9e9e9e"))
                obstacles.append(box)
        for eid, (kind, level) in dis.items():
            text = f"{kind.capitalize()} - {level}"
            box = hazard_label_box(g.edges[eid], text, obstacles)
            obstacles.append(box)
            labels.append((box, text, hazard_font, LEVEL_COLOR[level]))
            # label yang tergeser jauh dari ruasnya diberi garis penunjuk ke tengah ruas
            mx, my = edge_mid(g.edges[eid])
            px, py = min(max(mx, box[0]), box[2]), min(max(my, box[1]), box[3])
            if math.hypot(px - mx, py - my) > 6:
                canvas.create_line(mx, my, px, py, fill=LEVEL_COLOR[level], width=1, dash=(2, 2))
                canvas.create_oval(mx - 2, my - 2, mx + 2, my + 2, fill=LEVEL_COLOR[level], outline="")
        # label digambar paling akhir supaya berada di lapisan paling atas
        for box, text, font, outline in labels:
            draw_label(box, text, font, outline)

    def show_results():
        res = state["results"]
        if not res:
            return
        shown = res[algo_var.get()]
        out = []
        if not shown["found"]:
            out.append("Tujuan tidak dapat dijangkau:\nsemua rute terhalang ruas yang ditutup.")
        else:
            out.append(f"Rute ({algo_var.get()}):")
            for i, eid in enumerate(shown["edges"]):
                road = g.edges[eid]
                from_id, to_id = shown["nodes"][i], shown["nodes"][i + 1]
                hazard = dis.get(eid)
                note = f" [{hazard[0]}, {hazard[1]}]" if hazard else ""
                out.append(f"{i + 1}. {from_id} \u2192 {to_id} lewat {road['road']}, "
                           f"{comma(road['dist'])} km{note}")
            out.append(f"\nTotal jarak : {comma(shown['dist'])} km\nTotal biaya : {comma(shown['cost'])}")
        out.append("\nPerbandingan algoritma")
        out.append(f"{'Algo':<5}{'Biaya':>8}{'Jarak':>8}{'Ekspansi':>10}{'ms':>8}")
        for a, r in res.items():
            ms = f"{r['ms']:.3f}"
            out.append(f"{a:<5}{comma(fc(r['cost'])):>8}{comma(fc(r['dist'])):>8}"
                       f"{r['expanded']:>10}{comma(ms):>8}")
        show("\n".join(out))

    def refresh():
        draw()
        show_results()

    def search(*_):
        s, t = label_to_id.get(start_var.get()), label_to_id.get(goal_var.get())
        state["results"] = {}
        if not s or not t:
            draw()
            show("Pilih titik awal dan tujuan.\n\n" + heuristic_note)
            return
        if s == t:
            draw()
            show("Titik awal dan tujuan tidak boleh sama.")
            return
        state["results"] = {a: run_algo(a, g, s, t, dis) for a in ALGOS}
        refresh()

    def on_start_selected(*_):
        goal_options = [label for label in all_goals if label != start_var.get()]
        cb_g["values"] = goal_options
        if goal_var.get() == start_var.get():
            goal_var.set(goal_options[0])
        search()

    def edge_dialog(eid):
        e = g.edges[eid]
        win = tk.Toplevel(root)
        win.title("Gangguan ruas")
        win.transient(root)
        win.grab_set()
        ttk.Label(win, padding=8, text=f"{e['road']}\n{g.nodes[e['a']]['name']} - "
                                       f"{g.nodes[e['b']]['name']} ({e['dist']:.2f} km)").grid(
            row=0, column=0, columnspan=2)
        cur = dis.get(eid, (KINDS[0], "ringan"))
        kind, level = tk.StringVar(value=cur[0]), tk.StringVar(value=cur[1])
        ttk.Label(win, text="Jenis").grid(row=1, column=0, padx=8, sticky="w")
        ttk.Combobox(win, textvariable=kind, values=KINDS, state="readonly").grid(row=1, column=1, padx=8)
        ttk.Label(win, text="Tingkat").grid(row=2, column=0, padx=8, sticky="w")
        ttk.Combobox(win, textvariable=level, values=["ringan", "berat", CLOSED],
                     state="readonly").grid(row=2, column=1, padx=8)

        def apply():
            dis[eid] = (kind.get(), level.get())
            win.destroy()
            search()

        def clear():
            dis.pop(eid, None)
            win.destroy()
            search()

        bar = ttk.Frame(win, padding=8)
        bar.grid(row=3, column=0, columnspan=2)
        ttk.Button(bar, text="Terapkan", command=apply).pack(side="left", padx=4)
        ttk.Button(bar, text="Hapus gangguan", command=clear).pack(side="left", padx=4)

    def on_click(ev):
        best = None
        for e in g.edges.values():
            a, b = g.nodes[e["a"]], g.nodes[e["b"]]
            d = seg_dist(ev.x, ev.y, a["sx"], a["sy"], b["sx"], b["sy"])
            if d < 8 and (best is None or d < best[0]):
                best = (d, e["id"])
        if best:
            edge_dialog(best[1])

    def on_resize(ev):
        if ev.width > 1 and ev.height > 1:
            layout(g, ev.width, ev.height)
            draw()

    canvas.bind("<Configure>", on_resize)
    canvas.bind("<Button-1>", on_click)
    cb_s.bind("<<ComboboxSelected>>", on_start_selected)
    cb_g.bind("<<ComboboxSelected>>", search)
    draw()
    show("Pilih titik awal dan tujuan.\n\n" + heuristic_note)
    root.mainloop()


# ----------------------------------------------------------------- main
if __name__ == "__main__":
    graph = load_graph()
    bad = check_heuristic(graph)
    if bad:
        note = f"PERINGATAN: heuristik tidak admissible pada ruas {', '.join(bad)}."
    else:
        note = f"Heuristik admissible: lolos pada {len(graph.edges)} ruas."
    print(f"{len(graph.nodes)} simpul, {len(graph.edges)} ruas, gamma = {GAMMA:g}. {note}")
    if "--eval" in sys.argv:
        evaluate(graph)
    else:
        background, map_note = load_background(graph)
        if map_note:
            print(map_note)
            note += "\n" + map_note
        run_gui(graph, note, background)