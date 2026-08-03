FROM python:3.11-slim

ENV HOME=/app
WORKDIR /app
ENV PATH="/opt/OrcaSlicer/bin:$PATH"

RUN apt-get update && apt-get install -y \
    curl \
    fuse3 \
    libgtk-3-0t64 \
    libgl1 \
    libegl1 \
    libgstreamer-plugins-base1.0-0 \
    libwebkit2gtk-4.1-0 \
    libjavascriptcoregtk-4.1-0 \
    libglu1-mesa \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

RUN ln -s /usr/lib/x86_64-linux-gnu/libwebkit2gtk-4.1.so.0 /usr/lib/x86_64-linux-gnu/libwebkit2gtk-4.0.so.37 \
    && ln -s /usr/lib/x86_64-linux-gnu/libjavascriptcoregtk-4.1.so.0 /usr/lib/x86_64-linux-gnu/libjavascriptcoregtk-4.0.so.18

RUN curl -L -o orca-slicer \
    https://github.com/SoftFever/OrcaSlicer/releases/download/v2.3.0/OrcaSlicer_Linux_AppImage_V2.3.0.AppImage \
    && chmod +x orca-slicer \
    && ./orca-slicer --appimage-extract \
    && rm orca-slicer && mv squashfs-root /opt/OrcaSlicer

RUN mkdir -p \
    $HOME/.local/share/orca-slicer/configs/filament \
    $HOME/.local/share/orca-slicer/configs/machine \
    $HOME/.local/share/orca-slicer/configs/process \
    $HOME/.local/share/orca-slicer/projects \
    $HOME/.config/OrcaSlicer

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY default-slicer-state.json default-slicer-state.json
COPY frontend/dist ./frontend/dist
COPY slice.py ./

EXPOSE 5000

CMD ["python", "slice.py", "--server"]
