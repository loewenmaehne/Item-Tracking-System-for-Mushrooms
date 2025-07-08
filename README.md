# Copyright (c) 2025 Julian Zienert
# SPDX-License-Identifier: MIT

Item Tracking System for Mushrooms

Code generated with assistance from DeepSeek-R1 AI (https://deepseek.com) (MIT License)

ITEM TYPES (Barcode Prefixes):
PiPi - PioPino
ChNu - Chestnut
KiOy - KingOyster
BlOy - BlueOyster
PiOy - PinkOyster
LiMa - Lionsmane
InVe - Inventory
StOr - Storage
MiSc - Miscellaneous

BARCODE FORMAT:
Item: XXXX_DD_MM_YY_GX_XXXX (e.g. PiPi_08_07_25_G1_0001)

PYTHON SETUP (Windows):
1. Download Python 3 from python.org/downloads
2. Install with "Add Python to PATH" checked
3. Verify installation:
   python --version

USAGE:
1. Place src/main.py in desired folder
2. Open Command Prompt in that folder
3. Run: python main.py

BASIC COMMANDS:
1 - Check IN  2 - Check OUT
3 - Move items  4 - Add locations
7 - Create batch 9 - Exit
Type 'finish' to end scanning

DATA:
- Stores locally in item_tracking.db. Keep backuping this file!


DISCLAIMER: The author shall not be held liable for any damages or misuse of this software.  
Use at your own risk.  