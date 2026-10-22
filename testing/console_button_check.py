"""Manual, one-tap console diagnostic. No model or meter actions.

Select this file in Gtuner CV with Null Video Input and its matching GPC.
From PowerShell: py -3.10 console_button_check.py --tap A
"""
import argparse
import socket
import time

PORT = 26741


def decode(data, now):
    try:
        prefix, button, timestamp = data.decode('ascii').split(':')
        age = now - float(timestamp)
        if prefix == 'T2TEST' and button in ('A', 'X') and 0 <= age <= .5:
            return {'A': 1, 'X': 2}[button]
    except (ValueError, UnicodeError):
        pass
    return 0


class GCVWorker:
    def __init__(self, width, height):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('127.0.0.1', PORT))
        self.socket.setblocking(False)
        self.reset_next = False
        print('CONSOLE TEST: listening on 127.0.0.1:26741; waiting for manual A/X request.', flush=True)

    def process(self, frame):
        command = 0
        # Always send a neutral callback between requests for rising-edge GPC handling.
        neutral = self.reset_next
        self.reset_next = False
        for _ in range(8):
            try:
                data, _ = self.socket.recvfrom(128)
            except BlockingIOError:
                break
            decoded = decode(data, time.time())
            if not neutral and not command:
                command = decoded
        if command:
            self.reset_next = True
            print('CONSOLE TEST: forwarding one ' + ('A' if command == 1 else 'X') + ' tap; Xbox receipt is not confirmed.', flush=True)
        return frame, bytearray((84, 49, command))

    def __del__(self):
        sock = getattr(self, 'socket', None)
        if sock is not None:
            sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tap', required=True, choices=('A', 'X'))
    parser.add_argument('--port',type=int,default=PORT,help='Loopback test port (normally 26741)')
    args = parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Port must be 1 through 65535')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(('T2TEST:' + args.tap + ':' + str(time.time())).encode('ascii'), ('127.0.0.1', args.port))
    print('Sent one ' + args.tap + ' request. Check Gtuner output and the Xbox screen; sending alone does not confirm delivery.')


if __name__ == '__main__':
    main()
