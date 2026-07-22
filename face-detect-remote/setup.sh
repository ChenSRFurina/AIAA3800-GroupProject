#!/bin/bash

set -e

TORCH_INDEX="${VPET_TORCH_INDEX:-https://download.pytorch.org/whl/cu130}"

conda install -y "ffmpeg" -c conda-forge
pip install --upgrade torch torchvision --index-url "$TORCH_INDEX"
pip install -r ./requirements