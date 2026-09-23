import torch
import numpy as np
import random
from pathlib import Path

from tgnn.graphs.synthetic_graph_builder import build_graph
from tgnn.graphs.simulator import simulate
from tgnn.utils.helpers import nx_to_pyg
from tgnn.models.tgnn import TGNN


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Using device:", device)


# ---- LOAD MODEL ----
model = TGNN(input_dim=7).to(device)

model_path = Path(__file__).resolve().parents[1] / "models" / "tgnn.pth"

model.load_state_dict(
    torch.load(model_path, map_location=device)
)

model.eval()


# ---- BUILD GRAPH ----
G = build_graph()
num_nodes = len(G.nodes)


# ---- REALISTIC STRESS INJECTION (DISTRIBUTED, NOT CLUSTERED) ----
for node in G.nodes:
    r = random.random()

    if r < 0.7:
        # healthy
        G.nodes[node]["damage"] = random.uniform(0.1, 0.3)
        G.nodes[node]["load"] = random.uniform(0.3, 0.6)

    elif r < 0.9:
        # stressed
        G.nodes[node]["damage"] = random.uniform(0.3, 0.6)
        G.nodes[node]["load"] = random.uniform(0.6, 0.9)

    else:
        # critical
        G.nodes[node]["damage"] = random.uniform(0.7, 1.0)
        G.nodes[node]["load"] = random.uniform(0.9, 1.3)

    G.nodes[node]["stress"] = (
        G.nodes[node]["damage"] + G.nodes[node]["load"]
    )


# ---- SIMULATION ----
seq = simulate(G, T=5)


# ---- DEBUG: GRAPH STATE ----
print("\n=== INFERENCE GRAPH STATE ===")

for t, g_nx in enumerate(seq):
    failed = sum(
        1
        for n in g_nx.nodes
        if g_nx.nodes[n]["status"] == 0
    )

    stress_vals = [
        g_nx.nodes[n]["stress"]
        for n in g_nx.nodes
    ]

    print(
        f"  t={t} | failed={failed}/{num_nodes} | "
        f"stress={min(stress_vals):.3f}->{max(stress_vals):.3f}"
    )


# ---- CONVERT TO PYG ----
pyg_seq = [
    nx_to_pyg(g).to(device)
    for g in seq
]


# ---- INFERENCE ----
with torch.no_grad():
    preds = model(pyg_seq[:-1])


risk = (
    torch.sigmoid(preds[-1])
    .squeeze()
    .cpu()
    .numpy()
)


# ---- STATS ----
print("\n[DEBUG] Risk Stats:")
print(
    f"Min: {risk.min():.4f}, "
    f"Max: {risk.max():.4f}, "
    f"Mean: {risk.mean():.4f}"
)


# ---- RANKING ----
ranking = sorted(
    enumerate(risk),
    key=lambda x: x[1],
    reverse=True
)


print("\nTop 10 High Risk Nodes:")

for i, r in ranking[:10]:
    node_type = G.nodes[i]["type"]
    print(
        f"Node {i:3d} ({node_type:8s}) -> Risk {r:.4f}"
    )


print("\nBottom 5 Low Risk Nodes:")

for i, r in ranking[-5:]:
    node_type = G.nodes[i]["type"]
    print(
        f"Node {i:3d} ({node_type:8s}) -> Risk {r:.4f}"
    )