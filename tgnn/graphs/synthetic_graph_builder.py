import networkx as nx
import random
import math

NODE_TYPES = ["power", "road", "hospital", "telecom", "water", "social"]

DEPENDENCIES = {
    "hospital": ["power", "road"],
    "telecom": ["power"],
    "water": ["power"],
    "social": ["telecom"],
    "road": [],
    "power": []
}

def distance(a, b):
    return math.sqrt((a[0] - b[0])**2 + (a[1] - b[1])**2)

def build_graph():
    NUM_NODES = random.randint(40, 80)
    G = nx.DiGraph()

    for i in range(NUM_NODES):
        node_type = random.choice(NODE_TYPES)
        x = random.uniform(0, 1)
        y = random.uniform(0, 1)

        mode = random.random()

        if mode < 0.6:
            load = random.uniform(0.3, 0.6)
            damage = random.uniform(0.05, 0.2)
        elif mode < 0.85:
            load = random.uniform(0.6, 1.0)
            damage = random.uniform(0.2, 0.5)
        else:
            load = random.uniform(0.9, 1.3)
            damage = random.uniform(0.4, 0.8)

        capacity = random.uniform(0.7, 1.0)
        stress = load + damage
        utilization = load / capacity                  #currently redundant

        G.add_node(
            i,
            type=node_type,
            pos=(x, y),
            load=load,
            capacity=capacity,
            damage=damage,
            stress=stress,
            utilization=utilization,
            threshold=random.uniform(1.2, 1.6),
            resilience=random.uniform(0.8, 1.2),
            status=1
        )

    for i in G.nodes:
        for j in G.nodes:
            if i == j:
                continue

            dist = distance(G.nodes[i]["pos"], G.nodes[j]["pos"])

            if dist < 0.3 and random.random() < (1 - dist):
                G.add_edge(
                    i, j,
                    weight=1.0 - dist,
                    delay=random.randint(1, 3),
                    edge_type=0
                )

    for i in G.nodes:
        src_type = G.nodes[i]["type"]

        for j in G.nodes:
            if i == j:
                continue

            tgt_type = G.nodes[j]["type"]

            if src_type in DEPENDENCIES.get(tgt_type, []):
                G.add_edge(
                    i, j,
                    weight=random.uniform(0.8, 1.2),
                    delay=random.randint(1, 5),
                    edge_type=1
                )

    return G