import argparse
import csv
import logging
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional


# --- Data Structures ---

@dataclass
class NodeData:
    """Represents a single point in the OSM file."""
    id: str
    lat: float
    lon: float
    local_x: Optional[float] = None
    local_y: Optional[float] = None
    ele: Optional[float] = None


@dataclass
class LaneletBoundaryPoint:
    """Represents a row in the output CSV file."""
    lanelet_id: str
    way_id: str
    boundary_type: str  # 'left' or 'right'
    node_id: str
    sequence_order: int
    local_x: Optional[float]
    local_y: Optional[float]
    elevation: Optional[float]
    latitude: float
    longitude: float


# --- Logging Setup ---

def setup_logger() -> logging.Logger:
    """Configures the logger for console output."""
    logging.basicConfig(
        level=logging.INFO,
        format='[%(levelname)s] %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    return logging.getLogger(__name__)


# --- Parsing Logic ---

def parse_nodes(root: ET.Element) -> Dict[str, NodeData]:
    """
    Parses all <node> elements from the OSM tree.

    Args:
        root: The root element of the XML tree.

    Returns:
        A dictionary mapping node IDs to NodeData objects.
    """
    nodes = {}
    for node in root.findall('node'):
        node_id = node.get('id')
        if node_id is None:
            continue

        # Extract attributes
        lat = float(node.get('lat', 0.0))
        lon = float(node.get('lon', 0.0))
        
        local_x = None
        local_y = None
        ele = None

        # Extract tags
        for tag in node.findall('tag'):
            key = tag.get('k')
            val = tag.get('v')
            if val is None:
                continue

            if key == 'local_x':
                local_x = float(val)
            elif key == 'local_y':
                local_y = float(val)
            elif key == 'ele':
                ele = float(val)

        nodes[node_id] = NodeData(
            id=node_id, lat=lat, lon=lon,
            local_x=local_x, local_y=local_y, ele=ele
        )
    return nodes


def parse_ways(root: ET.Element) -> Dict[str, List[str]]:
    """
    Parses all <way> elements from the OSM tree.

    Args:
        root: The root element of the XML tree.

    Returns:
        A dictionary mapping way IDs to a list of node reference IDs.
    """
    ways = {}
    for way in root.findall('way'):
        way_id = way.get('id')
        if way_id:
            node_refs = [nd.get('ref') for nd in way.findall('nd') if nd.get('ref')]
            ways[way_id] = node_refs
    return ways


def extract_lanelet_boundaries(
    root: ET.Element,
    nodes: Dict[str, NodeData],
    ways: Dict[str, List[str]]
) -> List[LaneletBoundaryPoint]:
    """
    Extracts boundary points from Lanelet relations.

    Args:
        root: XML root element.
        nodes: Dictionary of parsed nodes.
        ways: Dictionary of parsed ways.

    Returns:
        A list of LaneletBoundaryPoint objects ready for CSV export.
    """
    output_data = []

    for relation in root.findall('relation'):
        # Check if relation is a Lanelet
        is_lanelet = False
        for tag in relation.findall('tag'):
            if tag.get('k') == 'type' and tag.get('v') == 'lanelet':
                is_lanelet = True
                break
        
        if not is_lanelet:
            continue

        lanelet_id = relation.get('id', 'unknown')
        
        # Iterate through members (left/right boundaries)
        for member in relation.findall('member'):
            role = member.get('role')
            way_id = member.get('ref')

            if role in ['left', 'right'] and way_id in ways:
                node_sequence = ways[way_id]
                
                for i, node_id in enumerate(node_sequence):
                    if node_id in nodes:
                        n = nodes[node_id]
                        point = LaneletBoundaryPoint(
                            lanelet_id=lanelet_id,
                            way_id=way_id,
                            boundary_type=role,
                            node_id=node_id,
                            sequence_order=i + 1,
                            local_x=n.local_x,
                            local_y=n.local_y,
                            elevation=n.ele,
                            latitude=n.lat,
                            longitude=n.lon
                        )
                        output_data.append(point)
    
    return output_data


def convert_osm_to_csv(input_path: Path, output_path: Path) -> None:
    """
    Orchestrates the conversion from .osm to .csv.

    Args:
        input_path: Path to the input .osm file.
        output_path: Path to the output .csv file.
    """
    logger = logging.getLogger(__name__)

    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return

    try:
        logger.info(f"Parsing XML file: {input_path}")
        tree = ET.parse(input_path)
        root = tree.getroot()

        # 1. Parse Nodes
        nodes = parse_nodes(root)
        logger.info(f"Loaded {len(nodes)} nodes.")

        # 2. Parse Ways
        ways = parse_ways(root)
        logger.info(f"Loaded {len(ways)} ways.")

        # 3. Extract Lanelet Boundaries
        data = extract_lanelet_boundaries(root, nodes, ways)
        
        if not data:
            logger.warning("No valid lanelet boundaries found with 'left'/'right' roles.")
            return

        # 4. Write to CSV
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        fieldnames = list(asdict(data[0]).keys())
        
        with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in data:
                writer.writerow(asdict(row))

        logger.info(f"Successfully created CSV: {output_path}")
        logger.info(f"Total points extracted: {len(data)}")

    except ET.ParseError:
        logger.error(f"Failed to parse XML file: {input_path}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='Parse Lanelet2 .osm file and export lane boundaries to CSV.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('input_file', type=Path, help='Path to input Lanelet2 .osm file')
    parser.add_argument('output_file', type=Path, help='Path to output .csv file')
    
    args = parser.parse_args()
    
    setup_logger()
    convert_osm_to_csv(args.input_file, args.output_file)


if __name__ == "__main__":
    main()
