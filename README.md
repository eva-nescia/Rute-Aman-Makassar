# Rute Aman Makassar

Route finder for Makassar that avoids road disruptions (demonstrations, floods, accidents) using A*,
with UCS and BFS as baselines. Built for the Artificial Intelligence course, Universitas Ciputra Makassar.

## Run

With [uv](https://docs.astral.sh/uv/) (no install step needed):

```
uv run --with openpyxl main.py            # interactive app (Tkinter)
uv run --with openpyxl main.py --eval     # run the 8 test scenarios + 10 random accident simulations
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

The chosen start is removed from the destination list. The map is drawn as a road-network diagram from each node's `x_km`, `y_km`; there is no background image.

## How to use the app

1. Pick a start (UC or MP) and a destination.
2. Click a road line on the diagram to mark a disruption (type and level: ringan, berat, ditutup).
3. The route is recalculated after every change. The side panel compares A*, UCS and BFS.

## Data

All graph data is in `data/makassar_route_dataset_v4.xlsx` (sheets Parameters, Nodes, Edges, Hazard Locations,
Test Scenarios). Add or edit nodes and edges there; no code change is needed. Keep only one `.xlsx` in `data/`.

Cost per edge: `cost(e) = distance_km + gamma * penalty`, with gamma = 2 (sheet Parameters).
Closed roads are removed from the graph.

## Files

```
main.py     program (graph loading, A*/UCS/BFS, Tkinter GUI, evaluation)
data/       dataset workbook
```

## Team

Kelompok 7 - Department of Informatics, Universitas Ciputra Makassar.
