import torch
import torch.nn.functional as F
from torch_geometric.nn import GATConv

class TGNN(torch.nn.Module):
    def __init__(self, input_dim=7, hidden_dim=128, num_types=6):
        super().__init__()

        self.type_embedding = torch.nn.Embedding(num_types, 8)

        gat_in = (input_dim - 1) + 8  # exclude status

        self.input_proj = torch.nn.Linear(gat_in, hidden_dim)

        self.gat1 = GATConv(
            hidden_dim,
            hidden_dim,
            heads=4,
            edge_dim=3,
            concat=True
        )

        self.gat2 = GATConv(
            hidden_dim * 4,
            hidden_dim,
            heads=1,
            edge_dim=3,
            concat=False
        )

        self.rnn = torch.nn.GRU(
            input_size=hidden_dim,
            hidden_size=hidden_dim,
            batch_first=False
        )

        self.fc = torch.nn.Sequential(
            torch.nn.Linear(hidden_dim, 32),
            torch.nn.ReLU(),
            torch.nn.Dropout(0.2),
            torch.nn.Linear(32, 1)
        )

        self.feature_norm = torch.nn.LayerNorm(gat_in)
        self.dropout = torch.nn.Dropout(0.2)

    def forward(self, graph_seq):
        all_x = []

        for data in graph_seq:
            edge_index = data.edge_index
            edge_attr  = data.edge_attr
            node_type  = data.node_type

            features = data.x[:, :-1]

            type_emb = self.type_embedding(node_type)
            x = torch.cat([features, type_emb], dim=1)

            x = self.feature_norm(x)

            # project to hidden dim for residual compatibility
            x = self.input_proj(x)   # [N, hidden]

            # GAT 1
            x1 = self.gat1(x, edge_index, edge_attr)
            x1 = F.elu(x1)
            x1 = self.dropout(x1)

            # GAT 2
            x2 = self.gat2(x1, edge_index, edge_attr)
            x2 = F.elu(x2)

            # residual (now valid)
            x = x + x2

            all_x.append(x)

        x_seq = torch.stack(all_x, dim=0)  # [T, N, hidden]

        out_seq, _ = self.rnn(x_seq)

        outputs = []
        for t in range(out_seq.shape[0]):
            outputs.append(self.fc(out_seq[t]))

        return outputs