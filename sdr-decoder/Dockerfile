# sdr-decoder — captura y decodificación DMR/LRRP real (dsd-fme + mbelib).
#
# Reemplaza el placeholder anterior. Compila dsd-fme/mbelib desde código
# fuente en un stage de build (misma receta ya validada manualmente en el
# host, ver sdr-decoder/INVESTIGACION_LRRP.md y
# dsd-fme/download-and-install-ubuntu2404lts.sh) y copia solo el binario +
# las librerías compartidas imprescindibles a un stage de runtime más
# liviano (confirmado con `ldd` sobre el binario real, no adivinado).
#
# Target único: Ubuntu (decisión del proyecto, no se contempla Windows).

# ---- Stage 1: build ----
FROM ubuntu:22.04 AS build

# Paquetes -dev necesarios para compilar mbelib + dsd-fme (misma lista que
# el script de instalación del propio repo de dsd-fme, confirmada contra
# lo instalado en el host de referencia con `dpkg -l`).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    ca-certificates \
    libcodec2-dev \
    libfftw3-dev \
    liblapack-dev \
    libncurses-dev \
    libpulse-dev \
    librtlsdr-dev \
    libsndfile1-dev \
    libusb-1.0-0-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# mbelib (fork lwvmobile, rama ambe_tones) — requisito de dsd-fme para
# decodificación de voz AMBE. Commit fijado al mismo que se usó en la
# compilación de referencia del host (ver INVESTIGACION_LRRP.md).
RUN git clone https://github.com/lwvmobile/mbelib.git && \
    cd mbelib && \
    git checkout ambe_tones && \
    git checkout 30dc79074ca022366a27d705b8023011d9600339 && \
    mkdir build && cd build && \
    cmake .. && \
    make -j"$(nproc)" && \
    make install && \
    ldconfig

# dsd-fme (lwvmobile/dsd-fme) — mismo commit que la compilación de
# referencia del host.
RUN git clone https://github.com/lwvmobile/dsd-fme.git && \
    cd dsd-fme && \
    git checkout 69d31151dd2d634cdc02aac4bff0e2a12c756ddb && \
    mkdir build && cd build && \
    cmake .. && \
    make -j"$(nproc)" && \
    make install && \
    ldconfig

# ---- Stage 2: runtime ----
FROM ubuntu:22.04

# Solo las librerías compartidas que `ldd /usr/local/bin/dsd-fme` reporta
# como imprescindibles (confirmado contra el binario real, no la lista
# completa de -dev). rtl-sdr trae el binario rtl_sdr que usa el bridge
# directamente (no solo la lib).
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-numpy \
    python3-scipy \
    rtl-sdr \
    libsndfile1 \
    libpulse0 \
    librtlsdr0 \
    libcodec2-1.0 \
    libflac8 \
    libvorbis0a \
    libvorbisenc2 \
    libopus0 \
    libogg0 \
    libusb-1.0-0 \
    libncursesw6 \
    libtinfo6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /usr/local/bin/dsd-fme /usr/local/bin/dsd-fme
COPY --from=build /usr/local/lib/libmbe.so* /usr/local/lib/
RUN ldconfig

WORKDIR /app
COPY live_presence_bridge.py iq_to_wav.py baofeng_gps_parser.py test_baofeng_gps_parser.py ./

# Directorios de trabajo del bridge (ver live_presence_bridge.py): bloques
# temporales de IQ/WAV (se borran solos) y logs crudos por bloque
# (persistentes — montados como volumen en docker-compose.yml).
RUN mkdir -p /app/bridge_blocks /app/logs

CMD ["python3", "live_presence_bridge.py"]
