import torch
from torch_geometric.data import Data

NODE_TYPE_MAP = {
    "power": 0, "road": 1, "hospital": 2,
    "telecom": 3, "water": 4, "social": 5
}

# Checkpoint contract. The first six columns are model inputs; `status` is the
# next-timestep training target and is excluded inside TGNN.forward().
NODE_FEATURE_ORDER = (
    "x_pos", "y_pos", "load", "capacity", "damage", "stress", "status"
)
MODEL_INPUT_FEATURE_ORDER = NODE_FEATURE_ORDER[:-1]
TARGET_FEATURE = NODE_FEATURE_ORDER[-1]

def nx_to_pyg(G):
    node_features = []
    node_types = []

    for _, data in G.nodes(data=True):
        x_pos, y_pos = data["pos"]

        values = {
            "x_pos": x_pos,
            "y_pos": y_pos,
            "load": data["load"],
            "capacity": data["capacity"],
            "damage": data["damage"],
            "stress": data["stress"],
            "status": data["status"],
        }
        node_features.append([values[name] for name in NODE_FEATURE_ORDER])

        node_types.append(NODE_TYPE_MAP[data["type"]])

    edge_index, edge_attr = [], []
    for u, v, edata in G.edges(data=True):
        edge_index.append([u, v])
        edge_attr.append([edata["weight"], edata["delay"], edata["edge_type"]])

    x = torch.tensor(node_features, dtype=torch.float)
    edge_index = torch.tensor(edge_index, dtype=torch.long).t().contiguous()
    edge_attr  = torch.tensor(edge_attr,  dtype=torch.float)
    node_types = torch.tensor(node_types, dtype=torch.long)

    return Data(x=x, edge_index=edge_index, edge_attr=edge_attr, node_type=node_types)
