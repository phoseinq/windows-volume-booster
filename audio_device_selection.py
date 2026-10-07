def find_output_index(devices, wanted, prefer_usb=False):
    """Return the best output-device index for a Windows-friendly name."""
    outputs = [
        (i, d) for i, d in enumerate(devices)
        if d.get('max_output_channels', 0) > 0
        and 'cable' not in d.get('name', '').lower()
    ]
    wanted = (wanted or '').lower().strip()

    for i, d in outputs:
        name = d.get('name', '').lower().strip()
        if wanted and (name.startswith(wanted) or wanted.startswith(name)):
            return i

    if prefer_usb:
        for i, d in outputs:
            name = d.get('name', '').lower()
            if 'usb' in name and ('speaker' in name or 'headphone' in name):
                return i

    return None
