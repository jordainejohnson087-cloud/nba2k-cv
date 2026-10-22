"""Manual console tap or timed X hold/release diagnostic. No model needed.

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
        if prefix == 'T2TEST' and button in ('A', 'X', 'RELEASE_X', 'HOLD_X') and 0 <= age <= .5:
            return {'A': 1, 'X': 2, 'RELEASE_X': 3, 'HOLD_X': 4}[button]
    except (ValueError, UnicodeError):
        pass
    return 0


class GCVWorker:
    def __init__(self, width, height):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('127.0.0.1', PORT))
        self.socket.setblocking(False)
        self.reset_next = False
        print('CONSOLE TEST: listening on 127.0.0.1:26741; waiting for A/X tap, HOLD_X or RELEASE_X.', flush=True)

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
            if decoded == 3 or (not neutral and not command):
                command = decoded
        if command:
            self.reset_next = True
            print('CONSOLE TEST: forwarding one ' + {1:'A tap',2:'X tap',3:'X release',4:'X hold'}[command] + '; Xbox receipt is not confirmed.', flush=True)
        return frame, bytearray((84, 49, command))

    def __del__(self):
        sock = getattr(self, 'socket', None)
        if sock is not None:
            sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--tap', choices=('A', 'X', 'RELEASE_X'))
    action.add_argument('--ai-x-shot', action='store_true', help='Start one armed AI-timed X hold through gtuner_meter')
    action.add_argument('--hold-x-ms', type=int, help='Hold X for 100..2000 ms, then release')
    parser.add_argument('--port',type=int,default=PORT,help='Loopback test port (normally 26741)')
    args = parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Port must be 1 through 65535')
    if args.hold_x_ms is not None and not 100<=args.hold_x_ms<=2000:
        parser.error('X hold must be 100 through 2000 ms')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        def send(action):
            sock.sendto(('T2TEST:' + action + ':' + str(time.time())).encode('ascii'), ('127.0.0.1', args.port))
        if args.ai_x_shot:
            send('AI_HOLD_X')
            print('Sent one AI X hold request. The armed meter bridge controls release; Xbox receipt is unverified.')
        elif args.hold_x_ms is not None:
            try:
                send('HOLD_X')
                time.sleep(args.hold_x_ms/1000)
            finally:
                send('RELEASE_X')
            print('Sent X HOLD then RELEASE after '+str(args.hold_x_ms)+' ms. Check Gtuner output and Xbox; sending does not confirm delivery.')
        else:
            send(args.tap)
            print('Sent one ' + args.tap + ' request. Check Gtuner output and the Xbox screen; sending alone does not confirm delivery.')


if __name__ == '__main__':
    main()
