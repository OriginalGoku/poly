#!/bin/bash
cd /home/lordgoku/poly
.venv/bin/python scripts/analyze_data_fitness.py data/*.db > logs/fitness_check_$(date +%Y%m%d_%H%M).log 2>&1
