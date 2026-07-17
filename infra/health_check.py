"""
Simple health check endpoint for containerized agents
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {'status': 'healthy', 'service': 'indigo-kg-agent'}
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress logging for health checks
        pass

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', 8000), HealthCheckHandler)
    print('Health check server running on port 8000...')
    server.serve_forever()
