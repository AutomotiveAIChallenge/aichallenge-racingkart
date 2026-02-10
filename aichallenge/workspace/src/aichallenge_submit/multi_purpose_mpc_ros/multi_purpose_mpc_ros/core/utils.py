from typing import List, Tuple
import pandas as pd

def format_time(seconds):
    minutes = int(seconds // 60)
    remaining_seconds = seconds % 60
    return f"{minutes:02}:{remaining_seconds:06.3f}"

def m_per_sec_to_kmh(m_per_sec: float) -> float:
    return m_per_sec * 3.6

def kmh_to_m_per_sec(kmh: float) -> float:
    return kmh / 3.6

def load_waypoints(csv_file_path: str) -> Tuple[List[float], List[float]]:
    df = pd.read_csv(csv_file_path)
    wp_x = df['wp_x'].tolist()
    wp_y = df['wp_y'].tolist()
    return wp_x, wp_y

def load_ref_path(csv_file_path: str):
    df = pd.read_csv(csv_file_path)
    x = df['x_m'].tolist()
    y = df['y_m'].tolist()
    psi = df['psi_rad'].tolist()
    kappa = df['kappa_radpm'].tolist()
    return x, y, psi, kappa