import subprocess

PACKAGE = "com.hwqgrhhjfd.idlefastfood"

result = subprocess.run(
    ["adb", "devices"], capture_output=True, text=True, check=True
)

devices = [
    line.split("\t")[0]
    for line in result.stdout.splitlines()[1:]
    if "device" in line
]

if not devices:
    print("No devices found. Please connect a device and try again.")
else:
    for device in devices:
        print(f"Opening app on device: {device}")
        subprocess.run(["adb", "-s", device, "shell", "monkey", "-p", PACKAGE, "1"])

    print(f"\nApp opened on {len(devices)} device(s).")