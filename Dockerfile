# FROM osrf/ros:humble-desktop AS common
FROM ghcr.io/automotiveaichallenge/autoware-universe:humble-latest AS common

COPY ./vehicle/zenoh-bridge-ros2dds_1.5.0_amd64.deb /tmp/
RUN apt install /tmp/zenoh-bridge-ros2dds_1.5.0_amd64.deb
COPY packages.txt /tmp/packages.txt
RUN apt-get update \
    && xargs -a /tmp/packages.txt apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Qt (rviz2) dark theme defaults via qt5ct
RUN mkdir -p /etc/xdg/qt5ct \
    && { \
      echo '[Appearance]'; \
      echo 'custom_palette=true'; \
      echo 'style=Fusion'; \
      echo 'color_scheme_path=/usr/share/qt5ct/colors/darker.conf'; \
    } > /etc/xdg/qt5ct/qt5ct.conf
ENV QT_QPA_PLATFORMTHEME=qt5ct

COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# Provide a robust `colcon` wrapper which avoids setuptools "entry script"
# dependency resolution issues (e.g. pkg_resources evaluating __requires__).
RUN cat >/usr/local/bin/colcon <<'EOF'
#!/usr/bin/env bash
exec python3 -c 'from colcon_core.command import main; import sys; sys.exit(main(argv=sys.argv[1:]))' "$@"
EOF
RUN chmod +x /usr/local/bin/colcon
ENV PATH=/usr/local/bin:${PATH}

# PATH="$PATH:/root/.local/bin"
# PATH="/usr/local/cuda/bin:$PATH"
ENV XDG_RUNTIME_DIR=/tmp/xdg
ENV ROS_LOCALHOST_ONLY=0
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ENV CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml

COPY vehicle/cyclonedds.xml /opt/autoware/cyclonedds.xml

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]

FROM common AS dev

RUN echo 'export PS1="\[\e]0;(AIC_DEV) ${debian_chroot:+($debian_chroot)}\u@\h: \w\a\](AIC_DEV) ${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ "' >> /etc/skel/.bashrc
RUN echo 'cd /aichallenge' >> /etc/skel/.bashrc
RUN echo 'eval $(resize)' >> /etc/skel/.bashrc
RUN echo 'source /docker-entrypoint.sh' >> /etc/skel/.bashrc
ENV RCUTILS_COLORIZED_OUTPUT=1

FROM common AS eval

ENV RCUTILS_COLORIZED_OUTPUT=0
ARG SUBMIT_TAR=submit/aichallenge_submit.tar.gz

COPY ${SUBMIT_TAR} /tmp/s.tgz
RUN git clone --depth 1 https://github.com/AutomotiveAIChallenge/aichallenge-racingkart /t \
 && mv /t/aichallenge /aichallenge \
 && rm -rf /aichallenge/simulator /aichallenge/workspace/src/aichallenge_submit /t \
 && chmod 757 /aichallenge \
 && tar zxf /tmp/s.tgz -C /aichallenge/workspace/src \
 && rm /tmp/s.tgz
COPY aichallenge/simulator/ /aichallenge/simulator/


RUN bash -c ' \
    source /autoware/install/setup.bash; \
    cd /aichallenge/workspace; \
    rosdep update; \
    rosdep install -y -r -i --from-paths src --ignore-src --rosdistro $ROS_DISTRO; \
    python3 -c "from colcon_core.command import main; import sys; sys.exit(main())" build --symlink-install --allow-overriding gyro_odometer --cmake-args -DCMAKE_BUILD_TYPE=Release'

CMD ["bash", "/aichallenge/run_evaluation.bash"]
