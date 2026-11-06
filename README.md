# Rute Aman Makassar

Route finder for Makassar that avoids road disruptions (demonstrations, floods, accidents) using A*,
with UCS and BFS as baselines. Built for the Artificial Intelligence course, Universitas Ciputra Makassar.

## Run

With [uv](https://docs.astral.sh/uv/) (no install step needed):

```
uv run --with openpyxl main.py            # interactive app (Tkinter)
uv run --with openpyxl main.py --eval     # run the 8 test scenarios + 10 random accident simulations
uv run --with openpyxl --with pillow main.py   # only needed when using a map background (see below)
```

Or with pip:

```
pip install -r requirements.txt
python main.py
python main.py --eval
```

Tkinter ships with the standard Python installer on Windows and macOS. On Ubuntu/Debian: `sudo apt install python3-tk`.

## Start and destination

| ID | Location | Start | Destination |
|---|---|---|---|
| UC | Universitas Ciputra Makassar | yes | yes |
| MP | Mall Panakkukang | yes | yes |
| Mari | Mall Ratu Indah | no | yes |
| TSM | Trans Studio Mall | no | yes |

The chosen start is removed from the destination list. The map is drawn as a road-network diagram from each node's `x_km`, `y_km`.

### Optional map background

1. Save a screenshot of the area (north up) as `data/peta.png`.
2. In sheet Nodes, add columns `x_px` and `y_px` and fill in the pixel position of at least 2 nodes in that
   screenshot (3 or more, spread out, e.g. UC, MP, TSM, is more accurate). Other rows can stay empty.
3. Install Pillow (`pip install pillow`) and run the app. The side panel shows the calibration error in pixels,
   and the "Tampilkan latar peta" checkbox turns the background on and off.

Without `data/peta.png` the app runs exactly as before. If the map is present but can't be used, the app says why.

## How to use the app

1. Pick a start (UC or MP) and a destination.
2. Click a road line on the diagram to mark a disruption (type and level: ringan, berat, ditutup).
3. The route is recalculated after every change. The side panel compares A*, UCS and BFS.

## Data

All graph data is in `data/makassar_route_dataset_v4.xlsx` (sheets Parameters, Nodes, Edges, Hazard Locations,
Test Scenarios). Add or edit nodes and edges there; no code change is needed. Keep only one `.xlsx` in `data/`.

Cost per edge: `cost(e) = distance_km + gamma * penalty`, with gamma = 2 (sheet Parameters).
Closed roads are removed from the graph.

## Evaluation (`--eval`)

Prints and saves to `hasil_evaluasi.xlsx` (sheets Skenario, Ringkasan, Simulasi Kecelakaan, Cek Google Maps):

- Optimality: A* cost = UCS cost; BFS for comparison
- Nodes expanded and average time (100 runs per scenario)
- Route safety: no closed roads on the route; which algorithms pass penalized roads
- Response accuracy: the route changes only when a disruption is on the best route, and matches the
  `route changed?` column of sheet Test Scenarios
- 10 random accidents on UC -> MP (fixed seed 42)
- Graph distances vs Google Maps for the routes in sheet Route Check (largest difference)

## Files

```
main.py               program (graph loading, A*/UCS/BFS, Tkinter GUI, evaluation)
data/                 dataset workbook (+ optional peta.png)
hasil_evaluasi.xlsx   created by --eval
```

## Team

Kelompok 7 - Department of Informatics, Universitas Ciputra Makassar.
