# test_ibkr_connection.py
"""
Standalone IBKR connection test utility.
Run this script to quickly test and diagnose IBKR connection issues.
"""

import sys
import socket
import time
from typing import Dict, Any, List, Tuple, Optional

try:
    from ib_insync import IB, util

    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    IB = None
    util = None


class IBKRConnectionTester:
    """Comprehensive IBKR connection testing utility"""

    def __init__(self):
        self.results = {}

    def run_full_diagnosis(self, port: int = 7496) -> Dict[str, Any]:
        """Run complete diagnosis and return results"""
        print(f"🔍 IBKR Connection Diagnosis (Port {port})")
        print("=" * 50)

        # Step 1: Check library availability
        print("\n📚 Checking ib_insync library...")
        lib_available = self._check_library()
        print(f"   ib_insync available: {'✅' if lib_available else '❌'}")

        if not lib_available:
            print("\n💡 Solution: pip install ib_insync")
            return {'library_available': False}

        # Step 2: Test network connectivity
        print("\n🌐 Testing network connectivity...")
        connectivity_results = self._test_connectivity(port)

        for result in connectivity_results:
            status = "✅" if result['success'] else "❌"
            latency = f" ({result['latency_ms']}ms)" if result['latency_ms'] else ""
            print(f"   {result['hostname']} ({result['family']} on {result['host']}): {status}{latency}")
            if result['error']:
                print(f"      Error: {result['error']}")

        # Step 3: Find best address
        best_address = self._find_best_address(connectivity_results)
        if best_address:
            host, port_used, family = best_address
            family_name = 'IPv6' if family == socket.AF_INET6 else 'IPv4'
            print(f"\n🎯 Best address found: {host}:{port_used} ({family_name})")
        else:
            print("\n❌ No working addresses found. Cannot proceed with API tests.")
            # **FIXED: Removed 'port' argument from the call**
            self._print_connectivity_help()
            return {'connectivity': False}

        # Step 4: Test API responsiveness
        print("\n🔌 Testing API responsiveness...")
        api_responsive = self._test_api_responsiveness(best_address)
        print(f"   API responsive: {'✅' if api_responsive else '❌'}")

        if not api_responsive:
            # **FIXED: Removed 'port' argument from the call**
            self._print_api_help()

        # Step 5: Test actual IB connection
        print("\n🚀 Testing actual IBKR connection...")
        connection_result = self._test_ib_connection(best_address)

        if connection_result['success']:
            print(f"   ✅ Connection successful!")
            print(f"   📊 API test: {connection_result['api_test']}")
        else:
            print(f"   ❌ Connection failed: {connection_result['error']}")
            self._print_connection_help()

        # Generate recommendations
        print("\n📝 Final Recommendations:")
        recommendations = self._generate_recommendations(
            connectivity_results, api_responsive, connection_result
        )
        for i, rec in enumerate(recommendations, 1):
            print(f"   {i}. {rec}")

        return {
            'library_available': lib_available,
            'connectivity_results': connectivity_results,
            'best_address': best_address,
            'api_responsive': api_responsive,
            'connection_result': connection_result,
            'recommendations': recommendations
        }

    def _check_library(self) -> bool:
        """Check if ib_insync is available"""
        return IBKR_AVAILABLE

    def _test_connectivity(self, port: int) -> List[Dict[str, Any]]:
        """Test connectivity to various addresses"""
        hosts_to_test = [
            ("::1", "IPv6 localhost"),
            ("127.0.0.1", "IPv4 localhost"),
        ]

        results = []

        for hostname, description in hosts_to_test:
            addresses = self._resolve_host(hostname, port)
            if not addresses:
                results.append({
                    'hostname': description, 'host': hostname, 'port': port,
                    'family': 'N/A', 'success': False, 'latency_ms': None,
                    'error': 'Could not resolve host'
                })
            for host, resolved_port, family in addresses:
                result = self._test_socket_connectivity(host, resolved_port, family, description)
                results.append(result)

        return results

    def _resolve_host(self, hostname: str, port: int) -> List[Tuple[str, int, int]]:
        """Resolve hostname to addresses"""
        addresses = []
        try:
            addr_infos = socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for family, _, _, _, sockaddr in addr_infos:
                if family in (socket.AF_INET, socket.AF_INET6):
                    addresses.append((sockaddr[0], sockaddr[1], family))
        except socket.gaierror:
            pass
        return addresses

    def _test_socket_connectivity(self, host: str, port: int, family: int, description: str) -> Dict[str, Any]:
        """Test socket connectivity with corrected IPv6 handling"""
        result = {
            'hostname': description, 'host': host, 'port': port,
            'family': 'IPv6' if family == socket.AF_INET6 else 'IPv4',
            'success': False, 'latency_ms': None, 'error': None
        }
        addr = None
        try:
            start_time = time.time()
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(3.0)

            if family == socket.AF_INET6:
                addr = (host, port, 0, 0)
            else:
                addr = (host, port)

            connect_result = sock.connect_ex(addr)
            sock.close()

            if connect_result == 0:
                result['success'] = True
                result['latency_ms'] = round((time.time() - start_time) * 1000, 2)
            else:
                result['error'] = f"Connection refused (error code {connect_result})"

        except socket.timeout:
            result['error'] = "Timeout"
        except Exception as e:
            result['error'] = f"Socket error for {addr}: {e}"

        return result

    def _find_best_address(self, connectivity_results: List[Dict[str, Any]]) -> Optional[Tuple[str, int, int]]:
        """Find the best working address"""
        working_results = [r for r in connectivity_results if r['success']]

        if not working_results:
            return None

        ipv6_results = sorted([r for r in working_results if r['family'] == 'IPv6'],
                              key=lambda x: x['latency_ms'] or 999)
        if ipv6_results:
            best = ipv6_results[0]
        else:
            ipv4_results = sorted([r for r in working_results if r['family'] == 'IPv4'],
                                  key=lambda x: x['latency_ms'] or 999)
            best = ipv4_results[0]

        family = socket.AF_INET6 if best['family'] == 'IPv6' else socket.AF_INET
        return (best['host'], best['port'], family)

    def _test_api_responsiveness(self, address: Tuple[str, int, int]) -> bool:
        """Test if API is responsive with corrected IPv6 handling"""
        if not address: return False
        host, port, family = address
        addr = None
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(3.0)

            if family == socket.AF_INET6:
                addr = (host, port, 0, 0)
            else:
                addr = (host, port)

            sock.connect(addr)
            sock.send(b'API\0')
            sock.settimeout(2.0)
            response = sock.recv(100)
            sock.close()
            return len(response) > 0
        except Exception:
            return False

    def _test_ib_connection(self, address: Tuple[str, int, int]) -> Dict[str, Any]:
        """Test actual IB connection"""
        if not IBKR_AVAILABLE or not address:
            return {'success': False, 'error': 'ib_insync not available or no working address found'}

        host, port, _ = address

        try:
            if hasattr(util, 'logToConsole'): util.logToConsole(level=40)
            ib = IB()
            ib.connect(host=host, port=port, clientId=999, timeout=8)

            if ib.isConnected():
                api_test = f"Current time from server: {ib.reqCurrentTime()}"
                ib.disconnect()
                return {'success': True, 'api_test': api_test}
            else:
                return {'success': False, 'error': 'ib.connect call completed but isConnected() is false.'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _generate_recommendations(self, connectivity_results: List[Dict[str, Any]],
                                  api_responsive: bool, connection_result: Dict[str, Any]) -> List[str]:
        """Generate specific recommendations"""
        recommendations = []

        working_connections = [r for r in connectivity_results if r['success']]

        if not working_connections:
            recommendations.extend([
                "🚀 Start IB Gateway or TWS",
                "🔑 Login to your IBKR account in Gateway",
                "📍 Verify the IBKR mode port is 7496",
                "🔥 Check firewall settings",
                "🔄 Try restarting Gateway completely"
            ])
        elif not api_responsive:
            recommendations.extend([
                "⚙️ **Configure API in IB Gateway (This is your likely problem!)**",
                "   • Go to: File -> Global Configuration -> API -> Settings",
                "   • ✅ **CHECK** 'Enable ActiveX and Socket Clients'",
                "   • 🔢 Make sure 'Socket port' is set to 7496",
                "   • 🌐 Under 'Trusted IP Addresses', click 'Create' and add `::1` and `127.0.0.1`",
                "   • 🔄 Click OK and restart Gateway completely",
                "💬 Dismiss any popup dialogs in the Gateway application"
            ])
        elif not connection_result.get('success'):
            error = connection_result.get('error', '')
            if 'already' in error.lower() or 'duplicate' in error.lower():
                recommendations.append("🔢 Try a different Client ID in your script (e.g., 2, 3, 4, etc.)")
            elif 'timeout' in error.lower():
                recommendations.extend([
                    "⏱️ Connection timeout - try:",
                    "   • Restarting Gateway completely",
                    "   • Using a different Client ID",
                    "   • Checking for Gateway popup dialogs"
                ])
            else:
                recommendations.extend([
                    "🔄 Try restarting IB Gateway",
                    "🔢 Use a different Client ID",
                    "💬 Check for popup dialogs in Gateway"
                ])
        else:
            recommendations.extend([
                "✅ Everything looks good!",
                "🎯 Connection should now work in your main application",
                "💡 If issues persist, try different Client IDs"
            ])

        return recommendations

    def _print_connectivity_help(self):
        """Print connectivity troubleshooting help"""
        # **FIXED: Added useful help text**
        print("""
        ----------------------------------------------------
        💡 Troubleshooting Connectivity:
        1.  **Is IB Gateway or TWS Running?** - Make sure the application is open.
        2.  **Are You Logged In?** - You must be fully logged into your IBKR account within the Gateway.
        3.  **Correct Port?** - IBKR mode expects the local API socket on 7496.
        4.  **Firewall?** - Check if your system's firewall is blocking the connection on that port.
        5.  **Restart** - When in doubt, completely close and restart the IB Gateway application.
        ----------------------------------------------------
        """)

    def _print_api_help(self):
        """Print API configuration help"""
        # **FIXED: Added useful help text**
        print("""
        ----------------------------------------------------
        💡 Troubleshooting API Responsiveness (Your current issue!):
        The script can see the Gateway, but the Gateway is not responding to the API handshake.
        This is almost always a configuration issue inside the Gateway itself.

        **Solution:**
        1.  In IB Gateway, go to **File -> Global Configuration**.
        2.  On the left, go to **API -> Settings**.
        3.  **CHECK THE BOX** for "Enable ActiveX and Socket Clients". This is required.
        4.  Make sure the "Socket port" number matches the port in the script (7496).
        5.  Under "Trusted IP Addresses", click "Create" and add `::1` and then `127.0.0.1`. This explicitly allows local connections.
        6.  Click **Apply** and **OK**.
        7.  **Completely restart the IB Gateway application** for the settings to take effect.
        ----------------------------------------------------
        """)

    def _print_connection_help(self):
        """Print connection troubleshooting help"""
        # **FIXED: Added useful help text**
        print("""
        ----------------------------------------------------
        💡 Troubleshooting Final Connection:
        The script can see the Gateway and the API is on, but the final login fails.

        **Common Causes:**
        1.  **Client ID in Use:** Another application is already connected using the same Client ID.
            -> Try changing the Client ID in your main script to a different number (e.g., 2, 3, 10).
        2.  **Gateway Pop-ups:** Check the IB Gateway application for any pop-up dialogs or messages that need to be clicked.
        3.  **Gateway is "Asleep":** Sometimes restarting the Gateway is the only fix.
        ----------------------------------------------------
        """)


def main():
    """Main test function"""
    import argparse

    parser = argparse.ArgumentParser(description="IBKR Connection Test Utility")
    parser.add_argument("--port", type=int, default=7496, help="Port to test (default: 7496)")

    args = parser.parse_args()

    try:
        tester = IBKRConnectionTester()
        results = tester.run_full_diagnosis(args.port)

        print(f"\n🎯 Test Summary:")
        print(f"   Library available: {'✅' if results.get('library_available') else '❌'}")

        if results.get('connectivity_results'):
            working_count = sum(1 for r in results['connectivity_results'] if r['success'])
            total_count = len(results['connectivity_results'])
            print(f"   Connectivity: {working_count}/{total_count} addresses working")

        if results.get('best_address'):
            host, port, family = results['best_address']
            family_name = 'IPv6' if family == socket.AF_INET6 else 'IPv4'
            print(f"   Best address: {host}:{port} ({family_name})")

        print(f"   API responsive: {'✅' if results.get('api_responsive') else '❌'}")
        print(f"   IB connection: {'✅' if results.get('connection_result', {}).get('success') else '❌'}")

    except KeyboardInterrupt:
        print("\n\n⏹️ Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")


if __name__ == "__main__":
    main()