# Settings Registry (canonical)

| Constant | Value |
|---|---|
| CFG_A000_TIMEOUT | 100 |
| CFG_B001_LIMIT | 107 |
| CFG_C002_SIZE | 114 |
| CFG_D003_TTL | 121 |
| CFG_E004_RATE | 128 |
| CFG_F005_DEPTH | 135 |
| CFG_G006_TIMEOUT | 142 |
| CFG_H007_LIMIT | 149 |
| CFG_I008_SIZE | 156 |
| CFG_J009_TTL | 163 |
| CFG_K010_RATE | 170 |
| CFG_L011_DEPTH | 177 |
| CFG_M012_TIMEOUT | 184 |
| CFG_N013_LIMIT | 191 |
| CFG_O014_SIZE | 198 |
| CFG_P015_TTL | 205 |
| CFG_Q016_RATE | 212 |
| CFG_R017_DEPTH | 219 |
| CFG_S018_TIMEOUT | 226 |
| CFG_T019_LIMIT | 233 |
| CFG_U020_SIZE | 240 |
| CFG_V021_TTL | 247 |
| CFG_W022_RATE | 254 |
| CFG_X023_DEPTH | 261 |
| CFG_Y024_TIMEOUT | 268 |
| CFG_Z025_LIMIT | 275 |
| CFG_A026_SIZE | 282 |
| CFG_B027_TTL | 289 |
| CFG_C028_RATE | 296 |
| CFG_D029_DEPTH | 303 |
| CFG_E030_TIMEOUT | 310 |
| CFG_F031_LIMIT | 317 |
| CFG_G032_SIZE | 324 |
| CFG_H033_TTL | 331 |
| CFG_I034_RATE | 338 |
| CFG_J035_DEPTH | 345 |
| CFG_K036_TIMEOUT | 352 |
| CFG_L037_LIMIT | 359 |
| CFG_M038_SIZE | 366 |
| CFG_N039_TTL | 373 |
| CFG_O040_RATE | 380 |
| CFG_P041_DEPTH | 387 |
| CFG_Q042_TIMEOUT | 394 |
| CFG_R043_LIMIT | 401 |
| CFG_S044_SIZE | 408 |
| CFG_T045_TTL | 415 |
| CFG_U046_RATE | 422 |
| CFG_V047_DEPTH | 429 |
| CFG_W048_TIMEOUT | 436 |
| CFG_X049_LIMIT | 443 |
| CFG_Y050_SIZE | 450 |
| CFG_Z051_TTL | 457 |
| CFG_A052_RATE | 464 |
| CFG_B053_DEPTH | 471 |
| CFG_C054_TIMEOUT | 478 |
| CFG_D055_LIMIT | 485 |
| CFG_E056_SIZE | 492 |
| CFG_F057_TTL | 499 |
| CFG_G058_RATE | 506 |
| CFG_H059_DEPTH | 513 |
| CFG_I060_TIMEOUT | 520 |
| CFG_J061_LIMIT | 527 |
| CFG_K062_SIZE | 534 |
| CFG_L063_TTL | 541 |
| CFG_M064_RATE | 548 |
| CFG_N065_DEPTH | 555 |
| CFG_O066_TIMEOUT | 562 |
| CFG_P067_LIMIT | 569 |
| CFG_Q068_SIZE | 576 |
| CFG_R069_TTL | 583 |
| CFG_S070_RATE | 590 |
| CFG_T071_DEPTH | 597 |
| CFG_U072_TIMEOUT | 604 |
| CFG_V073_LIMIT | 611 |
| CFG_W074_SIZE | 618 |
| CFG_X075_TTL | 625 |
| CFG_Y076_RATE | 632 |
| CFG_Z077_DEPTH | 639 |
| CFG_A078_TIMEOUT | 646 |
| CFG_B079_LIMIT | 653 |
| CFG_C080_SIZE | 660 |
| CFG_D081_TTL | 667 |
| CFG_E082_RATE | 674 |
| CFG_F083_DEPTH | 681 |
| CFG_G084_TIMEOUT | 688 |
| CFG_H085_LIMIT | 695 |
| CFG_I086_SIZE | 702 |
| CFG_J087_TTL | 709 |
| CFG_K088_RATE | 716 |
| CFG_L089_DEPTH | 723 |
| CFG_M090_TIMEOUT | 730 |
| CFG_N091_LIMIT | 737 |
| CFG_O092_SIZE | 744 |
| CFG_P093_TTL | 751 |
| CFG_Q094_RATE | 758 |
| CFG_R095_DEPTH | 765 |
| CFG_S096_TIMEOUT | 772 |
| CFG_T097_LIMIT | 779 |
| CFG_U098_SIZE | 786 |
| CFG_V099_TTL | 793 |
| CFG_W100_RATE | 800 |
| CFG_X101_DEPTH | 807 |
| CFG_Y102_TIMEOUT | 814 |
| CFG_Z103_LIMIT | 821 |
| CFG_A104_SIZE | 828 |
| CFG_B105_TTL | 835 |
| CFG_C106_RATE | 842 |
| CFG_D107_DEPTH | 849 |
| CFG_E108_TIMEOUT | 856 |
| CFG_F109_LIMIT | 863 |
| CFG_G110_SIZE | 870 |
| CFG_H111_TTL | 877 |
| CFG_I112_RATE | 884 |
| CFG_J113_DEPTH | 891 |
| CFG_K114_TIMEOUT | 898 |
| CFG_L115_LIMIT | 905 |
| CFG_M116_SIZE | 912 |
| CFG_N117_TTL | 919 |
| CFG_O118_RATE | 926 |
| CFG_P119_DEPTH | 933 |

# Reference Usage Notes

Every referenced constant must exist in the registry above.

- Service pipeline stage 0 reads `CFG_A000_TIMEOUT` at startup.
- Service pipeline stage 1 reads `CFG_I008_SIZE` at startup.
- Service pipeline stage 2 reads `CFG_Q016_RATE` at startup.
- Legacy importer still references `CFG_B027_LIMIT_MS` for batching.
- Service pipeline stage 3 reads `CFG_Y024_TIMEOUT` at startup.
- Service pipeline stage 4 reads `CFG_G032_SIZE` at startup.
- Service pipeline stage 6 reads `CFG_W048_TIMEOUT` at startup.
- Service pipeline stage 7 reads `CFG_E056_SIZE` at startup.
- Service pipeline stage 8 reads `CFG_M064_RATE` at startup.
- The cache warmer references `CFG_Q084_TTLX` when priming.
- Service pipeline stage 9 reads `CFG_U072_TIMEOUT` at startup.
- Service pipeline stage 10 reads `CFG_C080_SIZE` at startup.
- Service pipeline stage 11 reads `CFG_K088_RATE` at startup.
- Service pipeline stage 12 reads `CFG_S096_TIMEOUT` at startup.
- Service pipeline stage 13 reads `CFG_A104_SIZE` at startup.
- Service pipeline stage 14 reads `CFG_I112_RATE` at startup.
- The rate governor references `CFG_ZZ999_RATE` in burst mode.
