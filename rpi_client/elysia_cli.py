"""
Elysia AI - Raspberry Pi Terminal Client
Connects to PC server via WebSocket for chat
Run: python3 elysia_cli.py
Depends: pip3 install websocket-client
"""
import json
import sys
import threading
import time
import websocket

DEFAULT_SERVER_IP = "10.1.41.114"
DEFAULT_SERVER_PORT = 8080

# Terminal colors
PINK = "\033[95m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


class ElysiaCLI:
    def __init__(self):
        self.ws = None
        self.session_id = None
        self.connected = False
        self.streaming = False
        self.stream_start_time = 0
        self.waiting_response = False

    def print_banner(self):
        print(f"{PINK}{BOLD}")
        print("+--------------------------------------+")
        print("|     Elysia AI - Terminal Client      |")
        print("+--------------------------------------+")
        print(f"{RESET}")

    def connect(self, ip, port):
        ws_url = f"ws://{ip}:{port}/ws/chat?device_id=rpi&device_name=RaspberryPi"
        print(f"{YELLOW}Connecting to {ip}:{port}...{RESET}")

        connected_event = threading.Event()

        def on_open(ws):
            self.connected = True
            connected_event.set()
            print(f"\r{GREEN}Connected!{RESET}")

        def on_message(ws, message):
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")

                if msg_type == "connected":
                    self.session_id = data.get("session_id")

                elif msg_type == "stream":
                    content = data.get("content", "")
                    done = data.get("done", False)

                    if not done:
                        if not self.streaming:
                            self.streaming = True
                            self.stream_start_time = time.time()
                            sys.stdout.write(f"\n{PINK}{BOLD}Elysia:{RESET}\n")
                            sys.stdout.flush()
                        sys.stdout.write(f"{PINK}{content}{RESET}")
                        sys.stdout.flush()
                    else:
                        elapsed = int((time.time() - self.stream_start_time) * 1000) if self.stream_start_time else 0
                        tokens = data.get("tokens", 0)
                        if tokens:
                            sys.stdout.write(f"\n{CYAN}  ({tokens} tokens, {elapsed}ms){RESET}\n\n")
                        elif elapsed:
                            sys.stdout.write(f"\n{CYAN}  ({elapsed}ms){RESET}\n\n")
                        sys.stdout.flush()
                        self.streaming = False
                        self.waiting_response = False

            except json.JSONDecodeError:
                pass

        def on_error(ws, error):
            print(f"\n{RED}Error: {error}{RESET}")

        def on_close(ws, code, reason):
            self.connected = False
            self.streaming = False
            print(f"\n{YELLOW}Disconnected{RESET}")

        def run_ws():
            try:
                self.ws = websocket.WebSocketApp(
                    ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self.ws.run_forever()
            except Exception as e:
                print(f"{RED}Connection failed: {e}{RESET}")

        thread = threading.Thread(target=run_ws, daemon=True)
        thread.start()

        # Wait for connection
        return connected_event.wait(timeout=5)

    def send_message(self, msg):
        if not self.connected or not msg:
            return
        self.waiting_response = True
        payload = json.dumps({"type": "chat", "message": msg})
        try:
            self.ws.send(payload)
        except Exception as e:
            print(f"{RED}Send failed: {e}{RESET}")
            self.waiting_response = False

    def run(self):
        self.print_banner()

        # Get server address
        ip = input(f"{CYAN}Server IP [{DEFAULT_SERVER_IP}]: {RESET}").strip()
        if not ip:
            ip = DEFAULT_SERVER_IP

        port_str = input(f"{CYAN}Port [{DEFAULT_SERVER_PORT}]: {RESET}").strip()
        port = int(port_str) if port_str else DEFAULT_SERVER_PORT

        # Connect
        if not self.connect(ip, port):
            print(f"{RED}Connection timeout. Check if server is running.{RESET}")
            return

        print(f"{GREEN}Type a message to chat. Type /quit to exit.{RESET}")
        print()

        # Main loop
        while self.connected:
            try:
                sys.stdout.write(f"{GREEN}{BOLD}You:{RESET} ")
                sys.stdout.flush()
                msg = input().strip()

                if not msg:
                    continue
                if msg.lower() in ("/quit", "/exit", "/q"):
                    print(f"{YELLOW}Bye~{RESET}")
                    break

                self.send_message(msg)

                # Wait for response to complete
                while self.waiting_response and self.connected:
                    time.sleep(0.1)

            except KeyboardInterrupt:
                print(f"\n{YELLOW}Bye~{RESET}")
                break
            except EOFError:
                break

        # Cleanup
        if self.ws:
            self.ws.close()


def main():
    cli = ElysiaCLI()
    cli.run()

if __name__ == "__main__":
    main()
