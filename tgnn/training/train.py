import torch
from pathlib import Path

from tgnn.graphs.synthetic_graph_builder import build_graph
from tgnn.graphs.simulator import simulate
from tgnn.utils.helpers import nx_to_pyg
from tgnn.models.tgnn import TGNN


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("Using device:", device)

    model = TGNN(input_dim=7).to(device)  # 7 features now (added stress)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    # Decay LR by 0.5 if loss plateaus for 20 epochs
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=20, min_lr=1e-5
    )

    EPOCHS = 150
    T = 5                       # longer sequences = more failure cascades visible
    GRAPHS_PER_EPOCH = 4        # reuse multiple graphs per epoch for stable gradients

    for epoch in range(EPOCHS):
        epoch_loss = 0.0
        count = 0

        for _ in range(GRAPHS_PER_EPOCH):
            G = build_graph()
            seq = simulate(G, T=T)
            pyg_seq = [nx_to_pyg(g).to(device) for g in seq]

            preds = model(pyg_seq[:-1])

            all_targets = torch.cat([
                pyg_seq[t + 1].x[:, -1] for t in range(len(preds))
            ])

            n_neg = (all_targets == 0).sum().float()
            n_pos = (all_targets == 1).sum().float()
            pos_weight = (n_neg / (n_pos + 1e-6)).clamp(max=10.0)

            criterion = torch.nn.BCEWithLogitsLoss(
                pos_weight=pos_weight.to(device)
            )

            loss = sum(
                criterion(
                    preds[t],
                    pyg_seq[t + 1].x[:, -1].unsqueeze(1)
                )
                for t in range(len(preds))
            ) / len(preds)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            epoch_loss += loss.item()
            count += 1

        avg_loss = epoch_loss / count
        scheduler.step(avg_loss)

        print(
            f"Epoch {epoch + 1} | "
            f"Loss: {avg_loss:.4f} | "
            f"LR: {optimizer.param_groups[0]['lr']:.2e}"
        )

    model_path = Path(__file__).resolve().parents[1] / "models" / "tgnn.pth"
    torch.save(model.state_dict(), model_path)
    print(f"Saved model to: {model_path}")


if __name__ == "__main__":
    main()