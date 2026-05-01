import os
import termios
import time

port = "/dev/ttyAMA0"
fd = os.open(port, os.O_RDONLY | os.O_NOCTTY)
attrs = termios.tcgetattr(fd)
attrs[0] = termios.IGNPAR
attrs[1] = 0
attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
attrs[3] = 0
attrs[4] = termios.B9600
attrs[5] = termios.B9600
termios.tcsetattr(fd, termios.TCSANOW, attrs)

with os.fdopen(fd, "rb", buffering=0) as stream:
    end = time.time() + 8
    buf = b""
    while time.time() < end:
        b = stream.read(1)
        if b in (b"\n", b"\r"):
            if buf:
                line = buf.decode("ascii", errors="ignore").strip()
                if line:
                    print(repr(line))
                buf = b""
        else:
            buf += b
