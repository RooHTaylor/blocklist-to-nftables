# IP Blocklist to nftables

A Python utility for downloading IP address blocklists, parsing IPv4 and IPv6 networks, collapsing overlapping ranges, and generating an [nftables](https://www.netfilter.org/projects/nftables/index.html) set configuration.

The generated configuration can either be printed to stdout or written to a file. Optionally, the script can restart the `nftables` systemd service after generating the configuration.

## Features

* Download multiple IP blocklists from configurable URLs
* Support plain-text blocklists
* Support JSON blocklists with configurable IP address keys
* Parse both IPv4 and IPv6 addresses and CIDR networks
* Ignore blank lines and comments
* Ignore invalid IP/network entries
* Collapse overlapping and adjacent networks using Python's `ipaddress` module
* Generate separate nftables sets for IPv4 and IPv6
* Write generated nftables configuration to a file
* Optionally restart `nftables.service`
* Verbose/debug logging for troubleshooting

## Requirements

* Python 3.10+ (uses structural pattern matching)
* `requests`
* Linux with nftables installed if you intend to use the generated configuration
* `systemd` if using the `--restart` option

Install the Python dependency with:

```bash
pip install requests
```

## Usage

```bash
./blocklist-to-nftables.py -b blocklists.txt
```

By default, the generated nftables configuration is written to stdout.

To write the configuration to a file:

```bash
./blocklist-to-nftables.py \
    -b blocklists.txt \
    -n /etc/nftables.d/blocklist.nft
```

To enable debug logging:

```bash
./blocklist-to-nftables.py \
    -b blocklists.txt \
    -v
```

To restart nftables after generating the configuration:

```bash
./blocklist-to-nftables.py \
    -b blocklists.txt \
    -n /etc/nftables.d/blocklist.nft \
    -r
```

### Command-line options

| Option               | Description                                                          |
| -------------------- | -------------------------------------------------------------------- |
| `-b`, `--blocklists` | Path to the file containing blocklist URLs                           |
| `-n`, `--nftfile`    | File to which the generated nftables configuration should be written |
| `-r`, `--restart`    | Restart `nftables.service` using systemd after processing            |
| `-v`, `--verbose`    | Enable debug/verbose logging                                         |

A blocklist file must be provided with `-b` / `--blocklists`.

## Blocklist Configuration

Blocklists are specified one per line.

Blank lines and lines beginning with `#` are ignored.

The simplest format is a URL, or a URL with the text format specified in the `URL,FORMAT` format:

```text
https://example.com/blocklist.txt
https://example.com/blocklist.txt,text
```

Plain-text lists are the default format if no format is specified after the URL.

### Plain-text blocklists

Each line should contain an IPv4 address, IPv6 address, or CIDR network:

```text
192.0.2.1
192.0.2.0/24
2001:db8::1
2001:db8::/32
```

Comments can be included:

```text
192.0.2.0/24 # Example network
```

Invalid entries are skipped.

### JSON blocklists

JSON sources can be specified by appending the JSON key containing the IP address.

For example:

```text
https://example.com/blocklist.json,json.cidr
```

For a JSON document such as:

```json
{"cidr": "192.0.2.1"}
{"cidr": "192.0.2.0/24"}
{"cidr": "2001:db8::1"}
{"cidr": "2001:db8::/32"}
```

the `cidr` value will be parsed as the network.

The general syntax is:

```text
URL,json.KEY
```

For example:

```text
https://example.com/list.json,json.ip
https://example.com/list.json,json.cidr
https://example.com/list.json,json.network
```

Each downloaded line is expected to contain a JSON object with the specified key.

## Blocklist Processing

The processing pipeline is approximately:

```text
Blocklist configuration
        |
        v
Download blocklists
        |
        v
Parse text / JSON formats
        |
        v
Validate IP networks
        |
        v
Separate IPv4 and IPv6
        |
        v
Collapse overlapping networks
        |
        v
Generate nftables configuration
        |
        +----> stdout
        |
        +----> output file
        |
        v
Optional nftables restart
```

Networks are collapsed before generating the nftables configuration. This reduces redundant entries when multiple blocklists contain overlapping ranges.
The collapse step is arguably unnessesary, since nftables will do this automatically if auto-merge is enabled for the sets - which it is for these.

## Generated nftables Configuration

The script generates an `inet filter` table containing two interval sets:

* `blocklist_ips` for IPv4 addresses/networks
* `blocklist_ip6s` for IPv6 addresses/networks

The generated configuration has the following general structure:

```nft
table inet filter {

    set blocklist_ips {
        type ipv4_addr
        flags interval
        auto-merge
        elements = {
            ...
        }
    }

    set blocklist_ip6s {
        type ipv6_addr
        flags interval
        auto-merge
        elements = {
            ...
        }
    }
}
```

The sets are generated with nftables' `interval` and `auto-merge` flags so CIDR ranges can be represented efficiently.

## Using the Generated Configuration

If the script writes its output to an nftables configuration file, the resulting file can be loaded with `nft`.

For example:

```bash
sudo nft -f /etc/nftables.d/blocklist.nft
```

Alternatively, place the generated file in your system's nftables configuration structure and load it through your existing nftables configuration.

> **Note:** This script creates the nftables sets but does not create rules that reference them. You will need to add rules to your existing nftables configuration to actually block or otherwise process traffic matching these sets.

For example, a rule in an existing configuration could reference the IPv4 set:

```nft
ip saddr @blocklist_ips drop
```

and the IPv6 set:

```nft
ip6 saddr @blocklist_ip6s drop
```

Adapt these rules to your existing firewall policy rather than blindly adding them to a production firewall.

## Examples

### Generate configuration to stdout

```bash
./blocklist-to-nftables.py -b blocklists.txt
```

### Generate a configuration file

```bash
./blocklist-to-nftables.py \
    --blocklists blocklists.txt \
    --nftfile blocklist.nft
```

### Generate with debug logging

```bash
./blocklist-to-nftables.py \
    --blocklists blocklists.txt \
    --nftfile blocklist.nft \
    --verbose
```

### Generate and restart nftables

```bash
sudo ./blocklist-to-nftables.py \
    --blocklists /etc/blocklists.txt \
    --nftfile /etc/nftables.d/blocklist.nft \
    --restart
```

The `--restart` option invokes:

```text
/usr/bin/systemctl restart nftables.service
```

## Error Handling

Individual blocklists that cannot be downloaded are logged and skipped. This includes connection failures, timeouts, and non-HTTP-200 responses.

Invalid IP/network entries are also skipped rather than terminating the entire processing operation.

The program returns a non-zero exit status when required configuration is missing, the specified blocklist file does not exist, or a major processing/output/restart operation fails.

Use `--verbose` to enable debug logging when troubleshooting.

## Security Considerations

Blocklists are downloaded directly from the URLs supplied in the configuration file. Only use blocklist sources that you trust.

Because the generated output can be loaded directly into nftables, review generated configuration before deploying it to a production firewall, particularly when using third-party blocklists.

Be aware that large or frequently changing blocklists can produce substantial nftables configurations and may affect firewall reload times and system resources.

## Limitations

The current implementation expects:

* Text blocklists to contain one IP address/network per line.
* JSON blocklists to contain one JSON object per line.
* JSON IP addresses/networks to be located at the configured key.
* URLs to be directly accessible using HTTP(S).
* nftables to use the generated `inet filter` table and set names without conflicting with existing configuration.

The script does not itself install firewall rules that consume the generated sets.

## License

This project is licensed under the **GNU General Public License v3.0**.

See the [LICENSE](LICENSE) file for the full license text.

## Author

**Andrew Taylor**

September 2026
