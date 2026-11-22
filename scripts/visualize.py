"""Combine the per-level CSVs written by metrics.py into one summary table and a set of charts.

Run it from the repo root after metrics.py:

    python scripts/summarize.py                        # reads results/baseline (and results/optimized if it exists)
    python scripts/summarize.py --in results/baseline results/optimized --out results/summary

Needs:  pip install pandas matplotlib

What it writes into the output folder (default results/summary):
    summary.csv        one row per controller x period x demand level (mean over seeds, plus spread)
    worst_flows.csv    which movements were the longest-waiting in each run (counts)
    charts/*.png       one PNG per chart, numbered by group
    index.html         open this in a browser to browse every chart and the table in one page

How to read it
    * Every CSV row from metrics.py is ONE run (one seed). This script averages the seeds of each
      (controller, period, level). Lines in the charts are the mean over seeds; the pale band is
      min..max over seeds, so a wide band means the seeds disagree (noisy level).
    * Charts come in AM | PM pairs. If you have results for a second controller (e.g. your optimized
      one in results/optimized), every chart shows both controllers, and the "comparison" group
      appears: it pairs the two controllers seed by seed (same seed = same arriving traffic).
"""
import argparse
import html
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]

# ---- look: one blue/orange/aqua pair of series, quiet grid, dark-grey text --------------------------
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]                    # controller 1, 2, 3 in fixed order
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": GRID, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "axes.axisbelow": True,
    "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "legend.frameon": False, "figure.dpi": 100,
})

PERIODS = ["AM", "PM"]


# ====================================================================================================
# loading
# ====================================================================================================
def load(roots):
    frames = []
    for root in roots:
        files = sorted(Path(root).glob("*/scale_*.csv"))
        if not files:
            print(f"note: no scale_*.csv found under {root}")
        for f in files:
            frames.append(pd.read_csv(f))
    if not frames:
        sys.exit("no CSVs found. Run scripts/metrics.py first.")
    df = pd.concat(frames, ignore_index=True)
    df["pass"] = df["pass"].astype(str).str.lower().eq("true")
    df["scale_pct"] = pd.to_numeric(df["scale_pct"])
    for col in ("worst_wait_flow", "worst_entry", "notes"):
        df[col] = df[col].fillna("")
    return df


def controller_order(df):
    names = sorted(df["controller"].unique(), key=lambda c: (c != "sumo_actuated", c))
    return names


def pretty(c):
    return "baseline (SUMO actuated)" if c == "sumo_actuated" else c


# ====================================================================================================
# summary table
# ====================================================================================================
def mode_or_blank(s):
    s = s[s != ""]
    return s.value_counts().index[0] if len(s) else ""


def build_summary(df):
    rows = []
    for (ctl, per, pct), g in df.groupby(["controller", "period", "scale_pct"]):
        n = len(g)
        rows.append({
            "controller": ctl, "period": per, "scale_pct": pct, "seeds": n,
            "offered": g.offered.mean(),
            "finished_by_3600": g.finished_by_3600.mean(),
            "finished_pct": g.finished_pct.mean(),
            "backlog_mean": g.backlog_at_3600.mean(), "backlog_min": g.backlog_at_3600.min(),
            "backlog_max": g.backlog_at_3600.max(), "backlog_pct": g.backlog_pct.mean(),
            "delay_mean_s": g.mean_delay_s.mean(), "delay_std_s": g.mean_delay_s.std(ddof=0),
            "delay_min_s": g.mean_delay_s.min(), "delay_max_s": g.mean_delay_s.max(),
            "p95_delay_mean_s": g.p95_delay_s.mean(),
            "insertion_delay_s": g.mean_insertion_delay_s.mean(),
            "travel_time_s": g.mean_travel_time_s.mean(),
            "stops_per_veh": g.mean_stops.mean(),
            "mean_waiting_s": g.mean_waiting_s.mean(),
            "max_waiting_s": g.max_waiting_s.mean(),
            "n_over_300s": g.n_wait_over_300s.mean(), "n_over_600s": g.n_wait_over_600s.mean(),
            "clear_time_max_s": g.clear_time_s.max(), "teleports_max": g.teleports.max(),
            "collisions_max": g.collisions.max(),
            "seeds_passed": int(g["pass"].sum()), "pass_rate_pct": 100 * g["pass"].mean(),
            "most_common_worst_flow": mode_or_blank(g.worst_wait_flow),
        })
    out = pd.DataFrame(rows)
    out["_c"] = out.controller.map({c: i for i, c in enumerate(controller_order(df))})
    out["_p"] = out.period.map({p: i for i, p in enumerate(PERIODS)})
    return out.sort_values(["_c", "_p", "scale_pct"]).drop(columns=["_c", "_p"]).reset_index(drop=True)


def print_summary(s):
    for ctl in s.controller.unique():
        for per in PERIODS:
            t = s[(s.controller == ctl) & (s.period == per)]
            if t.empty:
                continue
            print(f"\n{pretty(ctl)}  {per}")
            print(f"{'level':>6}{'seeds':>6}{'offered':>8}{'finished':>9}{'backlog':>9}{'delay':>7}{'p95':>6}"
                  f"{'>600s':>7}{'clear+':>8}{'tele':>6}  pass")
            for _, r in t.iterrows():
                print(f"{r.scale_pct:>5g}%{r.seeds:>6d}{r.offered:>8.0f}{r.finished_pct:>8.0f}%{r.backlog_mean:>9.0f}"
                      f"{r.delay_mean_s:>6.0f}s{r.p95_delay_mean_s:>5.0f}s{r.n_over_600s:>7.0f}"
                      f"{r.clear_time_max_s:>7.0f}s{r.teleports_max:>6.0f}  {r.seeds_passed}/{r.seeds}")


# ====================================================================================================
# chart helpers
# ====================================================================================================
class Charts:
    def __init__(self, df, summary, out_dir):
        self.df, self.s = df, summary
        self.dir = Path(out_dir) / "charts"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ctls = controller_order(df)
        self.color = {c: SERIES[i % len(SERIES)] for i, c in enumerate(self.ctls)}
        self.periods = [p for p in PERIODS if p in set(df.period)]
        self.levels = sorted(df.scale_pct.unique())
        self.n = 0
        self.index = []                                           # (group, file, title, blurb)
        self.group = ""

    def fig(self, w=11.5, h=4.2, ncols=None):
        ncols = ncols or len(self.periods)
        fig, axes = plt.subplots(1, ncols, figsize=(w, h), sharey=False, squeeze=False)
        return fig, axes[0]

    def finish(self, fig, name, title, blurb, legend_ax=None):
        if len(self.ctls) > 1 and legend_ax is not None:
            h, l = legend_ax.get_legend_handles_labels()
            seen = {}
            for hh, ll in zip(h, l):
                seen.setdefault(ll, hh)
            fig.legend(seen.values(), seen.keys(), loc="upper right", ncol=len(seen), bbox_to_anchor=(0.995, 1.0))
        fig.suptitle(title, x=0.01, ha="left", fontsize=13, fontweight="bold", y=1.02)
        fig.tight_layout()
        self.n += 1
        fn = f"{self.n:02d}_{name}.png"
        fig.savefig(self.dir / fn, dpi=150, bbox_inches="tight")
        plt.close(fig)
        self.index.append((self.group, fn, title, blurb))

    def style_x(self, ax):
        ax.set_xticks(self.levels)
        ax.set_xticklabels([f"{l:g}%" for l in self.levels], fontsize=8.5)
        ax.set_xlabel("demand level (% of counted peak hour)")

    # ---- generic chart types -------------------------------------------------------------------
    def line(self, name, title, blurb, col, ylabel, how="mean", band=True, zero=True, ref=None, pct_axis=False):
        """col: column of the per-run CSV. how: 'mean' or 'max' across seeds. ref: optional per-run column drawn dashed grey."""
        fig, axes = self.fig()
        for ax, per in zip(axes, self.periods):
            d = self.df[self.df.period == per]
            for c in self.ctls:
                g = d[d.controller == c].groupby("scale_pct")[col]
                if g.ngroups == 0:
                    continue
                m = g.mean() if how == "mean" else g.max()
                ax.plot(m.index, m.values, color=self.color[c], lw=2, marker="o", ms=6,
                        markeredgecolor=SURFACE, markeredgewidth=1.5, label=pretty(c))
                if band and how == "mean":
                    ax.fill_between(m.index, g.min().values, g.max().values, color=self.color[c], alpha=0.13, lw=0)
            if ref:
                r = d.groupby("scale_pct")[ref].mean()
                ax.plot(r.index, r.values, color=INK2, lw=1.2, ls="--", label="offered (ideal)")
            ax.set_title(per)
            ax.set_ylabel(ylabel)
            if zero:
                ax.set_ylim(bottom=0)
            if pct_axis:
                ax.set_ylim(0, 105)
            self.style_x(ax)
        self.finish(fig, name, title, blurb, axes[0])

    def dots(self, name, title, blurb, col, ylabel):
        """Every seed as one dot, so you see the spread the mean hides."""
        fig, axes = self.fig()
        k = len(self.ctls)
        step = min(1.2, 2.2 / max(k, 1))
        for ax, per in zip(axes, self.periods):
            d = self.df[self.df.period == per]
            for i, c in enumerate(self.ctls):
                g = d[d.controller == c]
                off = (i - (k - 1) / 2) * step
                ax.scatter(g.scale_pct + off, g[col], s=34, color=self.color[c], alpha=0.85,
                           edgecolor=SURFACE, linewidth=1, label=pretty(c), zorder=3)
            ax.set_title(per)
            ax.set_ylabel(ylabel)
            ax.set_ylim(bottom=0)
            self.style_x(ax)
        self.finish(fig, name, title, blurb, axes[0])

    def capacity(self, name, title, blurb):
        """finished-by-3600 vs offered, one dot per run: below the diagonal = the network is not keeping up."""
        fig, axes = self.fig()
        for ax, per in zip(axes, self.periods):
            d = self.df[self.df.period == per]
            hi = max(d.offered.max(), d.finished_by_3600.max()) * 1.04
            ax.plot([0, hi], [0, hi], color=INK2, lw=1.2, ls="--", label="everything offered is served")
            for c in self.ctls:
                g = d[d.controller == c]
                ax.scatter(g.offered, g.finished_by_3600, s=34, color=self.color[c], alpha=0.85,
                           edgecolor=SURFACE, linewidth=1, label=pretty(c), zorder=3)
            ax.set_title(per)
            ax.set_xlabel("vehicles offered in the hour")
            ax.set_ylabel("vehicles finished by 3600 s")
            ax.set_xlim(0, hi)
            ax.set_ylim(0, hi)
        self.finish(fig, name, title, blurb, axes[0])

    def bars_counts(self, name, title, blurb, col, min_val, min_col, top=8):
        """Horizontal bars: in how many runs was <col> the worst one (only runs where min_col >= min_val)."""
        fig, axes = plt.subplots(len(self.ctls), len(self.periods),
                                 figsize=(11.5, 2.2 + 1.9 * len(self.ctls)), squeeze=False)
        for i, c in enumerate(self.ctls):
            for j, per in enumerate(self.periods):
                ax = axes[i][j]
                d = self.df[(self.df.controller == c) & (self.df.period == per) & (self.df[min_col] >= min_val)]
                cnt = d[col].value_counts().head(top)[::-1]
                ax.set_title(f"{per}" if i == 0 else "", loc="left")
                if cnt.empty:
                    ax.text(0.5, 0.5, "no run reached the threshold", ha="center", va="center",
                            color=INK2, transform=ax.transAxes)
                    ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
                    continue
                ax.barh(cnt.index, cnt.values, color=self.color[c], height=0.65)
                for y, v in enumerate(cnt.values):
                    ax.text(v, y, f" {v}", va="center", fontsize=8.5, color=INK2)
                ax.set_xlabel("number of runs" if i == len(self.ctls) - 1 else "")
                ax.tick_params(axis="y", labelsize=8.5)
                ax.grid(False)
                if j == 0 and len(self.ctls) > 1:
                    ax.set_ylabel(pretty(c), color=self.color[c], fontweight="bold")
        fig.suptitle(title, x=0.01, ha="left", fontsize=13, fontweight="bold", y=1.02)
        fig.tight_layout()
        self.n += 1
        fn = f"{self.n:02d}_{name}.png"
        fig.savefig(self.dir / fn, dpi=150, bbox_inches="tight")
        plt.close(fig)
        self.index.append((self.group, fn, title, blurb))

    def heatmap(self, name, title, blurb, kind):
        """Rows = controller x period, columns = demand level. kind: 'pass' or 'delay'."""
        rows = [(c, p) for c in self.ctls for p in self.periods]
        mat = np.full((len(rows), len(self.levels)), np.nan)
        txt = [[""] * len(self.levels) for _ in rows]
        for i, (c, p) in enumerate(rows):
            for j, l in enumerate(self.levels):
                r = self.s[(self.s.controller == c) & (self.s.period == p) & (self.s.scale_pct == l)]
                if r.empty:
                    continue
                r = r.iloc[0]
                if kind == "pass":
                    mat[i, j] = r.pass_rate_pct
                    txt[i][j] = f"{r.seeds_passed}/{r.seeds}"
                else:
                    mat[i, j] = r.delay_mean_s
                    txt[i][j] = f"{r.delay_mean_s:.0f}s"
        fig, ax = plt.subplots(figsize=(1.6 + 0.95 * len(self.levels), 1.4 + 0.55 * len(rows)))
        cmap = plt.get_cmap("Blues").copy()
        cmap.set_bad(SURFACE)
        vmax = 100 if kind == "pass" else np.nanmax(mat)
        ax.imshow(mat, cmap=cmap, vmin=0, vmax=vmax, aspect="auto")
        for i in range(len(rows)):
            for j in range(len(self.levels)):
                if txt[i][j]:
                    dark = mat[i, j] > 0.55 * vmax
                    ax.text(j, i, txt[i][j], ha="center", va="center", fontsize=9,
                            color="white" if dark else INK)
        ax.set_xticks(range(len(self.levels)))
        ax.set_xticklabels([f"{l:g}%" for l in self.levels])
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([f"{pretty(c) if len(self.ctls) > 1 else ''} {p}".strip() for c, p in rows])
        ax.grid(False)
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.set_xlabel("demand level (% of counted peak hour)")
        fig.suptitle(title, x=0.01, ha="left", fontsize=13, fontweight="bold", y=1.02)
        fig.tight_layout()
        self.n += 1
        fn = f"{self.n:02d}_{name}.png"
        fig.savefig(self.dir / fn, dpi=150, bbox_inches="tight")
        plt.close(fig)
        self.index.append((self.group, fn, title, blurb))

    def overview(self):
        """One page with the six headline numbers for AM and PM."""
        panels = [("mean_delay_s", "mean delay (s)"), ("p95_delay_s", "95th-pct delay (s)"),
                  ("backlog_at_3600", "backlog at 3600 s (veh)"), ("n_wait_over_600s", "vehicles stood >600 s"),
                  ("finished_pct", "finished by 3600 s (%)"), ("pass", "seeds passed (%)")]
        fig, axes = plt.subplots(len(self.periods), len(panels), figsize=(2.6 * len(panels), 2.9 * len(self.periods)),
                                 squeeze=False)
        for i, per in enumerate(self.periods):
            for j, (col, lab) in enumerate(panels):
                ax = axes[i][j]
                d = self.df[self.df.period == per]
                for c in self.ctls:
                    g = d[d.controller == c]
                    if col == "pass":
                        m = (100 * g.groupby("scale_pct")["pass"].mean())
                    else:
                        m = g.groupby("scale_pct")[col].mean()
                    ax.plot(m.index, m.values, color=self.color[c], lw=1.8, marker="o", ms=4.5,
                            markeredgecolor=SURFACE, markeredgewidth=1, label=pretty(c))
                ax.set_title(f"{per}: {lab}", fontsize=9)
                ax.set_ylim(bottom=0)
                ax.set_xticks(self.levels)
                ax.set_xticklabels([f"{l:g}" for l in self.levels], fontsize=6.5)
                ax.tick_params(axis="y", labelsize=7.5)
                if i == len(self.periods) - 1:
                    ax.set_xlabel("demand level (%)", fontsize=8)
        self.finish_grid(fig, "overview", "Overview: the headline numbers, AM and PM", "One-page summary.", axes[0][0])

    def finish_grid(self, fig, name, title, blurb, legend_ax):
        self.finish(fig, name, title, blurb, legend_ax)

    def line_pass(self):
        """Share of seeds that passed vs level (own function: 'pass' is a bool column)."""
        fig, axes = self.fig()
        for ax, per in zip(axes, self.periods):
            d = self.df[self.df.period == per]
            for c in self.ctls:
                m = 100 * d[d.controller == c].groupby("scale_pct")["pass"].mean()
                ax.plot(m.index, m.values, color=self.color[c], lw=2, marker="o", ms=6,
                        markeredgecolor=SURFACE, markeredgewidth=1.5, label=pretty(c))
            ax.set_title(per)
            ax.set_ylabel("% of seeds passed")
            ax.set_ylim(-3, 105)
            self.style_x(ax)
        self.finish(fig, "pass_rate", "Share of seeds that passed vs demand level",
                    "Strict rule: no backlog, no teleports, cleared in 10 min, nobody stood >10 min.", axes[0])

    # ---- paired comparison between controllers (same seeds) ------------------------------------
    def paired(self, name, title, blurb, col, ylabel, relative):
        base = self.ctls[0]
        others = self.ctls[1:]
        fig, axes = self.fig()
        for ax, per in zip(axes, self.periods):
            b = self.df[(self.df.controller == base) & (self.df.period == per)]
            ax.axhline(0, color=INK2, lw=1.2)
            for c in others:
                o = self.df[(self.df.controller == c) & (self.df.period == per)]
                m = b.merge(o, on=["period", "scale_pct", "seed"], suffixes=("_b", "_o"))
                if m.empty:
                    continue
                diff = (m[col + "_o"] - m[col + "_b"])
                if relative:
                    diff = 100 * diff / m[col + "_b"].replace(0, np.nan)
                m = m.assign(diff=diff)
                g = m.groupby("scale_pct")["diff"]
                ax.plot(g.mean().index, g.mean().values, color=self.color[c], lw=2, marker="o", ms=6,
                        markeredgecolor=SURFACE, markeredgewidth=1.5, label=f"{pretty(c)} minus baseline")
                ax.fill_between(g.mean().index, g.min().values, g.max().values, color=self.color[c], alpha=0.13, lw=0)
            ax.set_title(per)
            ax.set_ylabel(ylabel)
            self.style_x(ax)
        self.finish(fig, name, title, blurb, axes[0])


# ====================================================================================================
# the chart list
# ====================================================================================================
def make_all(ch, summary):
    two = len(ch.ctls) > 1

    ch.group = "Overview"
    ch.overview()
    ch.heatmap("pass_heatmap", "Pass/fail at a glance: seeds passed per level",
               "Each cell = seeds that passed the strict rule (no backlog, no teleports, cleared in 10 min, nobody stood >10 min).",
               "pass")
    ch.heatmap("delay_heatmap", "Mean delay per vehicle (s), every level",
               "Darker = longer mean delay. Good for spotting where the jump happens.", "delay")

    ch.group = "Delay and travel"
    ch.line("mean_delay", "Mean delay per vehicle vs demand level",
            "Time lost in the network plus time waiting to enter, averaged over vehicles and seeds. Band = min..max of seeds.",
            "mean_delay_s", "seconds")
    ch.line("p95_delay", "95th-percentile delay vs demand level",
            "What the unluckiest 5% of vehicles experience. Rises before the mean does.", "p95_delay_s", "seconds")
    ch.line("time_loss", "Time lost driving (excluding waiting to enter)",
            "Delay from signals and queues inside the network only.", "mean_time_loss_s", "seconds")
    ch.line("insertion_delay", "Time waiting outside the network to enter",
            "Non-zero means the entry roads are full: demand exceeds capacity.", "mean_insertion_delay_s", "seconds")
    ch.line("travel_time", "Mean travel time per vehicle", "Door to door inside the network.",
            "mean_travel_time_s", "seconds")
    ch.line("stops", "Stops per vehicle", "Average number of times a vehicle came to a stop.",
            "mean_stops", "stops per vehicle")
    ch.line("mean_waiting", "Mean standing time per vehicle", "Total time at speed under 0.1 m/s.",
            "mean_waiting_s", "seconds")

    ch.group = "Throughput and backlog"
    ch.line("backlog", "Backlog at 3600 s vs demand level",
            "Vehicles still waiting outside to enter when the hour ends. The clearest overload signal.",
            "backlog_at_3600", "vehicles")
    ch.line("backlog_pct", "Backlog as % of offered demand", "Same, relative to how many vehicles were offered.",
            "backlog_pct", "% of offered")
    ch.line("finished_pct", "Share of demand finished by 3600 s",
            "About 97% is healthy (the rest are still driving). Lower = vehicles not getting in or stuck.",
            "finished_pct", "% finished", zero=False, pct_axis=True)
    ch.line("throughput", "Throughput: vehicles finished by 3600 s",
            "Dashed line = vehicles offered. The gap between the two is what the network failed to serve in the hour.",
            "finished_by_3600", "vehicles per hour", ref="offered")
    ch.capacity("capacity_curve", "Throughput vs offered demand, one dot per run",
                "Dots on the dashed line = everything served. Dots bending below it show the capacity limit.")
    ch.line("in_network", "Vehicles inside the network at 3600 s",
            "Rises with load; plateaus when the network is full.", "in_network_at_3600", "vehicles")
    ch.line("clear_time", "Time to clear the network after demand ends (worst seed)",
            "Seconds after t=3600 until the last vehicle left. Pass rule needs 600 s or less.",
            "clear_time_s", "seconds", how="max")

    ch.group = "Fairness and starvation"
    ch.line("starved_600", "Vehicles that stood still >10 minutes",
            "A low mean delay can hide starved movements: this exposes them (the strict pass rule needs 0).",
            "n_wait_over_600s", "vehicles per run")
    ch.line("starved_300", "Vehicles that stood still >5 minutes", "The early-warning version of the chart above.",
            "n_wait_over_300s", "vehicles per run")
    ch.line("max_waiting", "Longest any single vehicle stood still", "Mean over seeds of each run's worst vehicle.",
            "max_waiting_s", "seconds")
    ch.line("max_insertion", "Longest any vehicle waited to enter", "Worst entry delay, mean over seeds.",
            "max_insertion_delay_s", "seconds")
    ch.bars_counts("worst_flows", "Which movement waited longest (runs where someone stood 300 s or more)",
                   "Each run names the flow of its longest-waiting vehicle. Taller = that movement is the recurring problem.",
                   "worst_wait_flow", 300, "max_waiting_s")
    ch.bars_counts("worst_entries", "Which entry road made vehicles wait longest to get in (mean 30 s or more)",
                   "Entry road with the largest mean insertion delay in each run.",
                   "worst_entry", 30, "worst_entry_delay_s")

    ch.group = "Reliability and safety"
    ch.line_pass()
    ch.line("teleports", "Teleports (worst seed)",
            "SUMO removes vehicles stuck too long. Any non-zero count means gridlock; distrust that run's other numbers.",
            "teleports", "teleports", how="max")
    ch.line("collisions", "Collisions (worst seed)", "Should be 0 everywhere.", "collisions", "collisions", how="max")
    ch.line("emergency", "Emergency braking events", "Rough signal of unsafe following or late signal changes.",
            "emergency_braking", "events per run")

    ch.group = "Seed-to-seed spread (every seed as one dot)"
    ch.dots("dots_delay", "Mean delay, every seed", "Wide vertical spread at a level = that level is noisy; use more seeds.",
            "mean_delay_s", "seconds")
    ch.dots("dots_p95", "95th-percentile delay, every seed", "", "p95_delay_s", "seconds")
    ch.dots("dots_backlog", "Backlog, every seed", "", "backlog_at_3600", "vehicles")
    ch.dots("dots_starved", "Vehicles that stood >10 min, every seed", "", "n_wait_over_600s", "vehicles")

    if two:
        ch.group = "Controller comparison (paired by seed: other minus baseline)"
        ch.paired("cmp_delay_pct", "Change in mean delay vs baseline (%)",
                  "Below 0 = your controller has less delay. Each seed is compared with the baseline on the SAME seed.",
                  "mean_delay_s", "% change", True)
        ch.paired("cmp_p95_pct", "Change in 95th-percentile delay vs baseline (%)", "", "p95_delay_s", "% change", True)
        ch.paired("cmp_backlog", "Change in backlog vs baseline (vehicles)", "Below 0 = fewer vehicles stuck outside.",
                  "backlog_at_3600", "vehicles", False)
        ch.paired("cmp_starved", "Change in starved vehicles (>10 min) vs baseline", "",
                  "n_wait_over_600s", "vehicles per run", False)
        ch.paired("cmp_throughput", "Change in vehicles finished by 3600 s vs baseline", "Above 0 = more served.",
                  "finished_by_3600", "vehicles", False)
        ch.paired("cmp_stops", "Change in stops per vehicle vs baseline", "", "mean_stops", "stops", False)





# ====================================================================================================
# index.html
# ====================================================================================================
def write_index(out, summary, ch):
    tbl = summary.copy()
    show = ["controller", "period", "scale_pct", "seeds", "offered", "finished_pct", "backlog_mean", "delay_mean_s",
            "p95_delay_mean_s", "n_over_600s", "clear_time_max_s", "teleports_max", "seeds_passed", "most_common_worst_flow"]
    tbl = tbl[show].copy()
    for c in tbl.columns:
        if tbl[c].dtype == float:
            tbl[c] = tbl[c].round(1)
    parts = ["<!doctype html><meta charset='utf-8'><title>Baseline summary</title>",
             "<style>body{font:14px system-ui;margin:24px auto;max-width:1100px;color:#0b0b0b;background:#fcfcfb}"
             "h1{font-size:22px}h2{margin-top:36px;border-bottom:1px solid #e4e3df;padding-bottom:4px}"
             "img{max-width:100%;margin:6px 0 2px}p.b{color:#52514e;margin:0 0 22px}"
             "table{border-collapse:collapse;font-size:12px}td,th{border-bottom:1px solid #e4e3df;padding:3px 8px;text-align:right}"
             "th{position:sticky;top:0;background:#fcfcfb}td:first-child,th:first-child{text-align:left}</style>",
             "<h1>Baseline results summary</h1>",
             "<p class='b'>Every chart is a PNG in the charts folder. Lines are means over seeds; pale bands are min..max.</p>",
             "<h2>Summary table</h2>", tbl.to_html(index=False, border=0, na_rep="")]
    last = None
    for group, fn, title, blurb in ch.index:
        if group != last:
            parts.append(f"<h2>{html.escape(group)}</h2>")
            last = group
        parts.append(f"<img src='charts/{fn}' alt='{html.escape(title)}'>")
        if blurb:
            parts.append(f"<p class='b'>{html.escape(blurb)}</p>")
    (Path(out) / "index.html").write_text("\n".join(parts))


# ====================================================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="roots", nargs="+", default=None,
                    help="folders holding <period>/scale_<pct>.csv (default: results/baseline, plus results/optimized if present)")
    ap.add_argument("--out", default=str(REPO / "results" / "summary"))
    a = ap.parse_args()
    roots = a.roots
    if roots is None:
        roots = [str(REPO / "results" / "baseline")]
        if (REPO / "results" / "optimized").is_dir():
            roots.append(str(REPO / "results" / "optimized"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    df = load(roots)
    summary = build_summary(df)
    summary.round(2).to_csv(out / "summary.csv", index=False)
    print_summary(summary)

    wf = (df[df.max_waiting_s >= 300].groupby(["controller", "period", "worst_wait_flow"]).size()
          .rename("runs").reset_index().sort_values(["controller", "period", "runs"], ascending=[True, True, False]))
    wf.to_csv(out / "worst_flows.csv", index=False)

    ch = Charts(df, summary, out)
    make_all(ch, summary)
    write_index(out, summary, ch)
    print(f"\n{len(df)} runs, {len(ch.index)} charts.")
    print(f"open {out / 'index.html'} in a browser to browse everything; table is {out / 'summary.csv'}")


if __name__ == "__main__":
    main()