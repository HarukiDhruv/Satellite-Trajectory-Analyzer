import requests
import numpy as np
import plotly.graph_objects as go
from sgp4.api import Satrec, jday
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict
import dash
from dash import dcc, html, Output, Input

# --- Configuration ---
NORAD_IDS = [25544, 58215]  # ISS and a Starlink satellite
TIME_WINDOW_HOURS = 6.0
TIME_STEP_SECONDS = 60
COLLISION_THRESHOLD_KM = 50.0
EARTH_RADIUS_KM = 6371.0

# --- Helper Functions ---
def fetch_tle(norad_id: int) -> Optional[Tuple[str, str, str]]:
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=tle"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        lines = response.text.strip().splitlines()
        if len(lines) >= 3:
            name, line1, line2 = lines[0].strip(), lines[1].strip(), lines[2].strip()
            return name, line1, line2
    except requests.exceptions.RequestException:
        return None
    return None

def propagate_positions(line1: str, line2: str, start_time: datetime, hours: float, step_sec: int) -> np.ndarray:
    satellite = Satrec.twoline2rv(line1, line2)
    num_steps = int(hours * 3600 / step_sec)
    positions = []
    for i in range(num_steps):
        current_time = start_time + timedelta(seconds=i * step_sec)
        jd, fr = jday(current_time.year, current_time.month, current_time.day,
                      current_time.hour, current_time.minute, current_time.second)
        error, position, _ = satellite.sgp4(jd, fr)
        positions.append(position if error == 0 else (np.nan, np.nan, np.nan))
    return np.array(positions)

def create_earth_sphere(radius: float) -> go.Surface:
    u, v = np.linspace(0, 2*np.pi, 100), np.linspace(0, np.pi, 100)
    x = radius * np.outer(np.cos(u), np.sin(v))
    y = radius * np.outer(np.sin(u), np.sin(v))
    z = radius * np.outer(np.ones_like(u), np.cos(v))
    colorscale = [
        [0.0, 'rgb(30, 80, 180)'], [0.2, 'rgb(50, 120, 220)'],
        [0.4, 'rgb(210, 200, 140)'], [0.6, 'rgb(80, 140, 60)'],
        [0.8, 'rgb(60, 100, 40)'], [1.0, 'rgb(255, 255, 255)']
    ]
    return go.Surface(x=x, y=y, z=z, colorscale=colorscale, surfacecolor=z,
                      cmin=-radius, cmax=radius, showscale=False, hoverinfo='none')

# --- Main Script ---
start_time = datetime.utcnow()
sats: Dict[int, dict] = {}
for nid in NORAD_IDS:
    tle = fetch_tle(nid)
    if tle:
        sats[nid] = {
            'name': tle[0],
            'positions': propagate_positions(tle[1], tle[2], start_time, TIME_WINDOW_HOURS, TIME_STEP_SECONDS)
        }

# --- Collision Analysis (static calculation) ---
ids = list(sats.keys())
closest_events = []  # store events for live countdown
for i in range(len(ids)):
    for j in range(i + 1, len(ids)):
        s1, s2 = sats[ids[i]], sats[ids[j]]
        distances = np.linalg.norm(s1['positions'] - s2['positions'], axis=1)
        min_dist_idx = np.nanargmin(distances)
        min_dist_km = distances[min_dist_idx]

        time_of_approach = start_time + timedelta(seconds=int(min_dist_idx) * TIME_STEP_SECONDS)
        closest_events.append({
            "s1": s1["name"],
            "s2": s2["name"],
            "distance": min_dist_km,
            "time_of_approach": time_of_approach
        })

# --- Data Plotting ---
fig_data = [create_earth_sphere(EARTH_RADIUS_KM)]
colors = ['cyan', 'magenta', 'yellow', 'lime']

for i, (nid, data) in enumerate(sats.items()):
    pos = data['positions']
    fig_data.append(go.Scatter3d(x=pos[:, 0], y=pos[:, 1], z=pos[:, 2], mode='lines',
                                 line=dict(color=colors[i % len(colors)], width=2),
                                 name=f"{data['name']} Orbit"))
for i, (nid, data) in enumerate(sats.items()):
    pos = data['positions'][0]
    fig_data.append(go.Scatter3d(x=[pos[0]], y=[pos[1]], z=[pos[2]], mode='markers',
                                 marker=dict(size=8, color=colors[i % len(colors)], symbol='diamond'),
                                 name=data['name']))

fig = go.Figure(data=fig_data)
fig.update_layout(
    scene=dict(
        xaxis=dict(title='ECI X (km)', range=[-15000, 15000]),
        yaxis=dict(title='ECI Y (km)', range=[-15000, 15000]),
        zaxis=dict(title='ECI Z (km)', range=[-15000, 15000]),
        aspectmode='cube', bgcolor='rgb(10,10,25)'
    ),
    legend=dict(orientation="h", y=1.05)
)

# --- Dash App ---
app = dash.Dash(__name__)
app.layout = html.Div([
    html.H2("Satellite Collision Analysis", style={"textAlign": "center", "color": "white"}),

    html.Div(id="live-info", style={
        "backgroundColor": "#111",
        "padding": "12px 20px",
        "borderRadius": "10px",
        "margin": "0 auto 15px auto",
        "width": "60%",
        "textAlign": "center",
        "color": "white",
        "boxShadow": "0px 0px 10px rgba(255,255,255,0.2)"
    }),

    dcc.Interval(id="interval", interval=1000, n_intervals=0),

    dcc.Graph(figure=fig, style={"height": "600px"})
], style={"backgroundColor": "black", "color": "white", "fontFamily": "Arial"})

# --- Callbacks ---
@app.callback(
    Output("live-info", "children"),
    Input("interval", "n_intervals")
)
def update_info(n):
    lines = []
    now = datetime.utcnow()
    for ev in closest_events:
        time_left = ev["time_of_approach"] - now
        minutes_left = int(time_left.total_seconds() // 60)
        seconds_left = int(time_left.total_seconds() % 60)
        if time_left.total_seconds() <= 0:
            countdown = "⏳ Event Passed"
        else:
            countdown = f"{minutes_left} min {seconds_left} sec left"
        if ev["distance"] < COLLISION_THRESHOLD_KM:
            lines.append(html.P(f"🚨 ALERT: {ev['s1']} & {ev['s2']} | Dist: {ev['distance']:.2f} km | {countdown}"))
        else:
            lines.append(html.P(f"{ev['s1']} & {ev['s2']} SAFE  | Closest: {ev['distance']:.2f} km | {countdown}"))
    return lines

if __name__ == "__main__":
    app.run(debug=True)
