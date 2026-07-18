#!/bin/bash

set -e

conda install -y "ffmpeg" -c conda-forge
pip install -r ./requirements