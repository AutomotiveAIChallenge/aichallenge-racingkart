import os
from glob import glob

from setuptools import setup

package_name = "collision_guard"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.xml")),
        (os.path.join("share", package_name, "config"), glob("config/*.param.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="aichallenge",
    maintainer_email="i.kota2015@gmail.com",
    description="Longitudinal collision guard (AEB + adaptive slowdown) for karts and walls.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "collision_guard_node = collision_guard.collision_guard_node:main",
        ],
    },
)
