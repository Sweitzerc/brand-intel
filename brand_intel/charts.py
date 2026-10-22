"""
Plotly figures for the dashboard.

Tone is polarity, so it uses a diverging pair (blue = positive,
red = negative, gray = neutral). Counts are single-series blue.
Backgrounds stay transparent so the charts follow Gradio's light/dark theme.
"""

import plotly.graph_objects as go

POSITIVE = "#2a78d6"
NEGATIVE = "#e34948"
NEUTRAL = "#a9a8a2"
SERIES = "#2a78d6"
INK = "#7a7974"     # mid-gray reads on both light and dark surfaces
GRID = "rgba(128,128,128,0.18)"


def _layout(fig: go.Figure, title: str, height: int = 320) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=15)),
        height=height, margin=dict(l=8, r=16, t=44, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=INK, size=12), showlegend=False, bargap=0.35,
        hoverlabel=dict(font_size=12),
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=INK, automargin=True)
    return fig


def tone_color(v: float) -> str:
    return POSITIVE if v > 0.15 else NEGATIVE if v < -0.15 else NEUTRAL


def tone_by_group(rows: list[tuple[str, int, float]], title: str) -> go.Figure:
    """Horizontal diverging bars: (label, count, avg tone)."""
    rows = sorted(rows, key=lambda r: r[2])
    fig = go.Figure(go.Bar(
        x=[r[2] for r in rows], y=[r[0] for r in rows], orientation="h",
        marker=dict(color=[tone_color(r[2]) for r in rows], cornerradius=4),
        text=[f"{r[2]:+.2f}" for r in rows], textposition="outside", cliponaxis=False,
        customdata=[r[1] for r in rows],
        hovertemplate="<b>%{y}</b><br>Avg tone %{x:+.2f}<br>%{customdata} mentions<extra></extra>",
    ))
    fig.update_xaxes(range=[-1.35, 1.35], title="Average tone  (−1 negative · +1 positive)")
    return _layout(fig, title, height=max(220, 60 + 38 * len(rows)))


def counts(rows: list[tuple[str, int]], title: str) -> go.Figure:
    rows = list(reversed(rows))
    fig = go.Figure(go.Bar(
        x=[r[1] for r in rows], y=[r[0] for r in rows], orientation="h",
        marker=dict(color=SERIES, cornerradius=4),
        hovertemplate="<b>%{y}</b><br>%{x} mentions<extra></extra>",
    ))
    fig.update_xaxes(title="Mentions")
    return _layout(fig, title, height=max(240, 60 + 24 * len(rows)))


def trend(history: list[tuple[str, float, int]], query: str) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=[h[0] for h in history], y=[h[1] for h in history], mode="lines+markers",
        line=dict(color=SERIES, width=2), marker=dict(size=8, color=SERIES),
        customdata=[h[2] for h in history],
        hovertemplate="%{x}<br>Avg tone %{y:+.2f}<br>%{customdata} items<extra></extra>",
    ))
    fig.update_yaxes(range=[-1.05, 1.05], title="Average tone")
    fig.add_hline(y=0, line=dict(color=INK, width=1, dash="dot"))
    return _layout(fig, f"Tone over time: {query}")
