"""Pencarian Rute Aman Menghindari Gangguan Jalan di Makassar (A*, UCS, BFS).

Jalankan GUI      : python main.py
Jalankan evaluasi : python main.py --eval

Data dibaca langsung dari berkas .xlsx di folder data/ (sheet Parameters, Nodes,
Edges, Hazard Locations, Test Scenarios). Tanpa .xlsx, program membaca CSV dengan
nama sheet yang sama (nodes.csv, edges.csv, ...).
Peta digambar sebagai skema jaringan jalan dari kolom x_km, y_km pada sheet Nodes.
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
        return [{k.strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


def get(row, prefix, default=""):
    """Ambil nilai kolom yang namanya diawali prefix (mis. 'x_km' untuk 'x_km (east)')."""
    for k, v in row.items():
        if k.lower().startswith(prefix):
            return v
    return default


class Graph:
    def __init__(self, nodes, edges):
        self.nodes, self.edges = nodes, edges
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
                        dis=dis, expected=r.get("cost with hazard", "")))
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
def evaluate(g):
    scenarios = load_scenarios()
    if not scenarios:
        print("Sheet Test Scenarios kosong atau tidak ada.")
        return
    print(f"{'ID':<4}{'Skenario':<30}{'Harap':>7}{'UCS':>8}{'A*':>8}{'BFS':>8}"
          f"{'Opt':>5}{'Ubah':>6}{'Aman':>6}{'Eks B/U/A':>12}{'ms A*':>8}")
    n_opt = n_safe = n_exp = n_exp_ok = bfs_opt = 0
    exp_sum = dict.fromkeys(ALGOS, 0)
    n_with_route = 0
    ms_sum = dict.fromkeys(ALGOS, 0.0)
    for sc in scenarios:
        s, t, dis = sc["start"], sc["goal"], sc["dis"]
        res = {a: run_algo(a, g, s, t, dis) for a in ALGOS}
        base = ALGOS["A*"](g, s, t, {})
        same = lambda x, y: x == y or abs(x - y) < 1e-9
        opt = same(res["A*"]["cost"], res["UCS"]["cost"])
        changed = res["A*"]["nodes"] != base["nodes"]
        safe = all(dis.get(e, (0, ""))[1] != CLOSED for e in res["A*"]["edges"])
        mark = "-"
        if sc["expected"]:
            ok = same(num(sc["expected"]), res["UCS"]["cost"]) or \
                abs(num(sc["expected"]) - res["UCS"]["cost"]) < 0.011
            mark = "OK" if ok else "X"
            n_exp += 1
            n_exp_ok += ok
        has_route = res["A*"]["found"]
        n_safe += safe
        if has_route:
            n_opt += opt
            bfs_opt += same(res["BFS"]["cost"], res["UCS"]["cost"])
        n_with_route += has_route
        for a in ALGOS:
            if has_route:
                exp_sum[a] += res[a]["expanded"]
            ms_sum[a] += res[a]["ms"]
        print(f"{sc['id']:<4}{sc['name'][:28]:<30}{mark:>7}{fc(res['UCS']['cost']):>8}"
              f"{fc(res['A*']['cost']):>8}{fc(res['BFS']['cost']):>8}"
              f"{'Y' if opt else 'N':>5}{'Y' if changed else 'N':>6}{'Y' if safe else 'N':>6}"
              f"{res['BFS']['expanded']:>5}/{res['UCS']['expanded']}/{res['A*']['expanded']:<3}"
              f"{res['A*']['ms']:>8.3f}")
    n = len(scenarios)
    print(f"\nOptimalitas A* terhadap UCS : {n_opt}/{n_with_route} (skenario yang punya rute)")
    print(f"BFS optimal                 : {bfs_opt}/{n_with_route} (skenario yang punya rute)")
    print(f"Rute A* tidak melewati ruas ditutup: {n_safe}/{n}")
    if n_exp:
        print(f"Cocok dengan biaya di sheet Test Scenarios: {n_exp_ok}/{n_exp}")
    print(f"Rata-rata simpul diekspansi dihitung dari {n_with_route} skenario yang punya rute "
          f"(skenario tanpa rute tidak ikut dirata-rata):")
    for a in ALGOS:
        print(f"{a:<4} rata-rata simpul diekspansi {exp_sum[a] / max(n_with_route, 1):5.1f} "
              f"| rata-rata waktu {ms_sum[a] / n:.3f} ms (semua {n} skenario)")
    simulate_accidents(g)


def simulate_accidents(g, n=10, seed=42):
    """Kecelakaan acak (ruas mana pun, ringan/berat) pada rute UC -> MP, seed tetap."""
    s, t = "UC", "MP"
    if s not in g.nodes or t not in g.nodes:
        return
    rnd = random.Random(seed)
    edge_ids = list(g.edges)
    base = ALGOS["A*"](g, s, t, {})
    print(f"\nSimulasi kecelakaan acak UC -> MP (seed {seed}), biaya dasar {fc(base['cost'])}:")
    ok = 0
    for k in range(1, n + 1):
        eid, level = rnd.choice(edge_ids), rnd.choice(["ringan", "berat"])
        dis = {eid: ("kecelakaan", level)}
        a, u = ALGOS["A*"](g, s, t, dis), ALGOS["UCS"](g, s, t, dis)
        same = a["cost"] == u["cost"] or abs(a["cost"] - u["cost"]) < 1e-9
        ok += same
        print(f"  {k:>2}. {eid} {level:<7} A*={fc(a['cost'])} UCS={fc(u['cost'])} "
              f"{'berubah' if a['nodes'] != base['nodes'] else 'tetap':<8}{'OK' if same else 'GAGAL'}")
    print(f"  A* = UCS pada {ok}/{n} simulasi")


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


def run_gui(g, heuristic_note):
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import ttk

    root = tk.Tk()
    root.title("Rute Aman Makassar - A*")
    dis, state = {}, {"results": {}}

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

    def first_free_box(centers, text, font, obstacles):
        """Kotak label pada posisi pertama yang tidak menimpa simpul atau label lain."""
        boxes = [label_box(cx, cy, text, font) for cx, cy in centers]
        for box in boxes:
            if not any(boxes_overlap(box, other) for other in obstacles):
                return box
        return boxes[0]

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
        return first_free_box(centers, text, location_font, obstacles)

    def hazard_label_box(edge, text, obstacles):
        """Label gangguan: di tengah ruas, atau digeser sedikit bila menutupi simpul."""
        a, b = g.nodes[edge["a"]], g.nodes[edge["b"]]
        mid_x, mid_y = (a["sx"] + b["sx"]) / 2, (a["sy"] + b["sy"]) / 2
        step_x = hazard_font.measure(text) / 2
        step_y = hazard_font.metrics("linespace") + 4
        shifts = sorted(((col, row) for row in range(-3, 4) for col in range(-2, 3)),
                        key=lambda shift: abs(shift[0]) + abs(shift[1]))
        centers = [(mid_x + col * step_x, mid_y + row * step_y) for col, row in shifts]
        return first_free_box(centers, text, hazard_font, obstacles)

    def draw():
        canvas.delete("all")
        shown = state["results"].get(algo_var.get())
        on_route = set(shown["edges"]) if shown and shown["found"] else set()
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
        # label digambar paling akhir supaya berada di lapisan paling atas
        for eid, (kind, level) in dis.items():
            text = f"{kind.capitalize()} - {level}"
            box = hazard_label_box(g.edges[eid], text, obstacles)
            draw_label(box, text, hazard_font, LEVEL_COLOR[level])
            obstacles.append(box)
        for n in g.nodes.values():
            if n["kind"] == "location":
                box = location_label_box(n, n["id"], obstacles)
                draw_label(box, n["id"], location_font, "#9e9e9e")
                obstacles.append(box)

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
        run_gui(graph, note)