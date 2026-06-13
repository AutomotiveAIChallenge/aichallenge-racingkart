import os
from glob import glob

from setuptools import setup

package_name = "race_judge_py"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name, f"{package_name}.geometry", f"{package_name}.logic"],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.xml")),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Taiki Tanaka",
    maintainer_email="taiki.tanaka@tier4.jp",
    description="On-vehicle race judgment (lap, rank, collision, wall) for real racing karts",
    license="Apache License 2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "vehicle_judge_node = race_judge_py.vehicle_judge_node:main",
            "race_director_node = race_judge_py.race_director_node:main",
        ],
    },
)
