#!/usr/bin/env python3
"""
Download and parse ip address blocklists, importing them into an nftables set
Author: Andrew Taylor <andrew@taylorlane.ca>
Date: September 2026
"""

import argparse
import logging
import sys
from typing import Optional
from pathlib import Path
import requests
import tempfile
import ipaddress
import subprocess
import json

# Setup logger configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def parse_arguments(args: list[str]) -> argparse.Namespace:
    """
    Parses command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="""Download and parse ip address blocklists, importing them 
into an nftables set"""
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Increase output verbosity"
    )

    parser.add_argument(
        "-b", "--blocklists",
        help="File containting the blocklist urls"
    )

    parser.add_argument(
        "-n", "--nftfile",
        help="File to write nftables set data"
    )

    parser.add_argument(
        "-r", "--restart",
        action="store_true",
        help="Restarts nftables via systemd service after blocklist is installed"
    )

    return parser.parse_args(args)


def download_blocklists(
    blocklists_file: Path
) -> list[tuple[str, tempfile._TemporaryFileWrapper]]:
    """
    Open the file that contains the blocklists and parse them into a list of 
    urls. Try each url to download the list to a temporary file.
    """

    logger.debug(f"Opening {blocklists_file}")
    blocklists_fd = open(blocklists_file, "r", encoding="utf-8")
    
    blocklist_temp_files = []
    for line in blocklists_fd:
        tline = line.strip()
        # Skip blank lines or commented lines
        if not tline or tline.startswith("#"):
            continue

        # Parse out inline comments
        data, *comment = tline.split("#", 1)
        # Parse out options
        url, *fmt = data.split(",", 1)
        # If fmt wasn't there, set it to text, otherwise stringify it
        if not fmt:
            logger.debug("No format. Defaulting to text")
            fmt = "text"
        elif fmt[0] == "text" or fmt[0].startswith("json."):
            fmt = fmt[0]
            logger.debug(f"List format: {fmt}")
        else:
            logger.error(f"Invalid list format provided! Skipping url: {data}")
            continue

        # Make the http request to download the blocklist file
        try:
            logger.debug(f"Making http request to {url}")
            r = requests.get(url, timeout=30)
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Could not access: {url} - {e}")
            continue
        except requests.exceptions.Timeout:
            logger.error(f"Could not access: {url} - Connection timed out")
            continue
        if r.status_code != 200:
            logger.error(f"Could not access: {url} - Returned {r.status_code}")
            continue
        
        # Write the request contents to a temporary file and save the fd to a
        # list to be returned for processing.
        t_fd = tempfile.NamedTemporaryFile(
            "w+t",
            encoding="utf-8",
            delete=True
        )
        logger.debug(f"Writing response to {t_fd.name}")
        t_fd.write(r.text)
        t_fd.flush()
        blocklist_temp_files.append((fmt, t_fd))

    logger.debug(f"Closing {blocklists_file}")
    blocklists_fd.close()

    return blocklist_temp_files


def process_text_blocklist_file(
    tfd: tempfile._TemporaryFileWrapper
) -> tuple[list[ipaddress.IPv4Network], list[ipaddress.IPv6Network]]:
    """
    Process a file, expecting ip address entries in each line
    """

    fd = open(tfd.name, "r", encoding="utf-8")

    ipv4_networks = []
    ipv6_networks = []
    for line in fd:
        tline = line.strip()
        # Skip blank lines or commented lines
        if not tline or tline.startswith("#"):
            continue
        # Parse out inline comments
        data, *comment = tline.split("#", 1)
        try:
            network = ipaddress.ip_network(data, strict=False)
        except ValueError:
            logger.debug(f"Invalid network {data}")
            continue
        match network.version:
            case 4:
                ipv4_networks.append(network)
            case 6:
                ipv6_networks.append(network)
    
    fd.close()

    return (ipv4_networks, ipv6_networks)


def process_json_blocklist_file(
    key: str,
    tfd: tempfile._TemporaryFileWrapper
) -> str:
    """
    Process a file, expecting json with ip addresses at {key}
    """

    fd = open(tfd.name, "r", encoding="utf-8")

    ipv4_networks = []
    ipv6_networks = []
    for line in fd:
        tline = line.strip()
        # Skip blank lines or commented lines
        if not tline or tline.startswith("#"):
            continue
        # Parse out inline comments
        data, *comment = tline.split("#", 1)
        try:
            json_data = json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Error converting to JSON: {e} {data}")
            continue

        if key not in json_data:
            logger.warn(f"Key {key} not found in {json_data}")
            continue

        try:
            network = ipaddress.ip_network(json_data[key], strict=False)
        except ValueError:
            logger.debug(f"Invalid network {json_data[key]}")
            continue
        match network.version:
            case 4:
                ipv4_networks.append(network)
            case 6:
                ipv6_networks.append(network)
    
    fd.close()

    return (ipv4_networks, ipv6_networks)


def parse_blocklists(
    blocklist_temp_files: list[tuple[str, tempfile._TemporaryFileWrapper]]
) -> tuple[list[ipaddress.IPv4Network], list[ipaddress.IPv6Network]]:
    """
    From a [ (str, _TemporaryFileWrapper), ...] formatted list, parse each 
    blocklist using the appropriate method. For example, if the fmt is json.cdir
    then parse the list using json, expecting the ip addresses to be at the key 
    cidr. No fmt will assume text, which assumes each line will contain an 
    ipaddress or network.
    Format the ip networks into an nftables string that will be executed.
    """

    ipv4_networks = []
    ipv6_networks = []
    for t_bl in blocklist_temp_files:
        logger.debug(f"Processing {t_bl[1].name}")
        if t_bl[0] == "text":
            new_ipv4_networks, new_ipv6_networks = process_text_blocklist_file(t_bl[1])
        elif t_bl[0].startswith("json."):
            _, key = t_bl[0].split(".", 1)
            new_ipv4_networks, new_ipv6_networks = process_json_blocklist_file(key, t_bl[1])
        else:
            raise ValueError(f"An invalid list format was encountered!")
        ipv4_networks.extend(new_ipv4_networks)
        ipv6_networks.extend(new_ipv6_networks)
    
    return (ipv4_networks, ipv6_networks)


def generate_nft_set(
    ipv4_networks: list[ipaddress.IPv4Network],
    ipv6_networks: list[ipaddress.IPv6Network]
):
    logger.debug("Generating nfttables set")

    nft_string = """table inet filter {

    set blocklist_ips {
        type ipv4_addr
        flags interval
        auto-merge
        elements = {
"""

    for network in ipv4_networks:
        network_string = str(network)
        nft_string += f"            {network_string},\n"

    nft_string += """        }
    }

    set blocklist_ip6s {
        type ipv6_addr
        flags interval
        auto-merge
        elements = {
"""

    for network in ipv6_networks:
        network_string = str(network)
        nft_string += f"            {network_string},\n"

    nft_string += """        }
    }
}
"""

    return nft_string


def write_nft_file(
    filename: str,
    ipv4_networks: list[ipaddress.IPv4Network],
    ipv6_networks: list[ipaddress.IPv6Network]
):

    logger.debug(f"Writing nft set to {filename}")
    fd = open(filename, "w", encoding="utf-8")
    fd.write(generate_nft_set(ipv4_networks, ipv6_networks))
    fd.close()

    return


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parsed_args = parse_arguments(argv)
    
    if parsed_args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled.")
    
    if not parsed_args.blocklists:
        logger.error(f"Provide a blocklist url file with -b/--blocklists!")
        return 1
    
    blocklists_path = Path(parsed_args.blocklists)
    if not blocklists_path.is_file():
        logger.error(f"{blocklists_path} does not exist or is not a file!")
        return 1
    
    try:
        blocklist_temp_files = download_blocklists(blocklists_path)
    except Exception as e:
        logger.error(f"Error downloading blocklists: {e}")
        return 1
    
    try:
        ipv4_networks, ipv6_networks = parse_blocklists(blocklist_temp_files)
    except Exception as e:
        logger.error(f"Error parsing blocklists: {e}")
        return 1
    
    logger.debug(f"Parsed {len(ipv4_networks)} IPv4 networks and {len(ipv6_networks)} IPv6 networks")
    ipv4_networks = list(ipaddress.collapse_addresses(ipv4_networks))
    ipv6_networks = list(ipaddress.collapse_addresses(ipv6_networks))
    logger.debug(f"Collapsed to {len(ipv4_networks)} IPv4 networks and {len(ipv6_networks)} IPv6 networks")

    if not parsed_args.nftfile:
        print(generate_nft_set(ipv4_networks, ipv6_networks))
    else:
        try:
            write_nft_file(parsed_args.nftfile, ipv4_networks, ipv6_networks)
        except Exception as e:
            logger.error(f"Error writing nft set: {e}")
            return 1

    if parsed_args.restart:
        logger.debug("Restarting nftables via systemd")
        try:
            r = subprocess.run(
                ["/usr/bin/systemctl", "restart", "nftables.service"],
                check=True,
            )
            #r.check_returncode()
        except Exception as e:
            logger.error(f"Error restarting nftables: {e}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
