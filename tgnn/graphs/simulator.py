import copy
import random
import math

FAILURE_K = 6
RECOVERY_PROB = 0.08

def simulate(G, T=10):
    graphs = []

    for t in range(T):
        current = {
            node: {
                'load':   G.nodes[node]['load'],
                'damage': G.nodes[node]['damage'],
                'status': G.nodes[node]['status'],
            }
            for node in G.nodes
        }

        for node in G.nodes:
            load   = current[node]['load']
            damage = current[node]['damage']
            status = current[node]['status']

            threshold  = G.nodes[node]['threshold']
            resilience = G.nodes[node]['resilience']

            neighbor_failures = sum(
                1 for n in G.predecessors(node)
                if current[n]['status'] == 0
            )

            # weighted propagation
            for n in G.predecessors(node):
                if current[n]['status'] == 0:
                    weight = G[n][node]['weight']
                    damage += 0.03 * weight
                    load   += 0.02 * weight

            # noise
            damage += random.uniform(0.0, 0.01)
            load   += random.uniform(0.0, 0.01)

            # decay
            damage *= 0.96
            load   *= 0.96

            damage = min(max(damage, 0.0), 1.2)
            load   = min(max(load,   0.0), 1.5)

            stress = damage + load

            prob = 1 / (1 + math.exp(-FAILURE_K * ((stress - threshold) / resilience)))

            if status == 1:
                if random.random() < prob:
                    status = 0
            else:
                if random.random() < RECOVERY_PROB:
                    status = 1

            G.nodes[node]['damage'] = damage
            G.nodes[node]['load']   = load
            G.nodes[node]['stress'] = stress
            G.nodes[node]['status'] = status

        graphs.append(copy.deepcopy(G))

    return graphs
