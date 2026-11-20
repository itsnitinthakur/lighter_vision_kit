# Licences of the free tools used

Everything in `replacement/` and `tools/` uses only these. None charges a fee to ship inside a machine you sell.
Not legal advice: have this list checked once before the first sale, and ship the licence texts with the machine.

| Component | Licence | Inside a machine you sell |
| --- | --- | --- |
| Python | PSF | Free |
| OpenCV 4.5+ / 5 | Apache 2.0 | Free; include its licence text |
| NumPy | BSD-3 | Free |
| pymodbus | BSD-3 | Free |
| pytest (tests only, not shipped) | MIT | Free |

Planned additions:

| Component | Licence | Note |
| --- | --- | --- |
| PySide6 (operator screen) | LGPL-3 | Free if used unmodified as a library |
| RF-DETR (if AI detection is ever needed) | Apache 2.0 | Free |

Avoid unless you pay:

| Component | Licence | Problem |
| --- | --- | --- |
| Ultralytics YOLO v8 / 11 / 26 | AGPL-3.0 | Must publish all your source code, or buy their Enterprise licence |
| PyQt | GPL-3 | Same problem; use PySide6 |
| Adaptive Vision Library, Halcon | Commercial | Paid licence per machine |

Camera SDKs (Hikrobot MVS, Dahua MV, MindVision, Basler pylon) are free to use with their own cameras;
ship only the runtime files their licence allows.

Sources: [OpenCV licence](https://opencv.org/opencv-is-to-change-the-license-to-apache-2/) ·
[RF-DETR](https://blog.roboflow.com/rf-detr-is-free-to-use-commercially/) ·
[Ultralytics licence](https://www.ultralytics.com/license)
