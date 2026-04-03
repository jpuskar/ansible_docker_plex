"""
Custom Ansible filter for selecting Talos images based on requirements
"""


def _to_bool(value):
    """Normalize a value to a Python bool.

    Ansible/Jinja2 can pass booleans as strings ('true', 'True', 'yes', etc.)
    depending on how variables flow through templates.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ('true', 'yes', '1')
    return bool(value)


def select_talos_image(images_info, requirements):
    """
    Select the first image from images_info that matches all requirements.

    Args:
        images_info: List of dicts with 'name', 'image', and 'tags' keys
        requirements: Dict with required tag values (secureboot, version, extensions, arch)

    Returns:
        String with the image URL, or None if no match found
    """
    for image in images_info:
        tags = image.get('tags', {})
        match = True

        # Check secureboot requirement (compare as bools to handle str/bool mismatch)
        if 'secureboot' in requirements:
            if _to_bool(tags.get('secureboot')) != _to_bool(requirements['secureboot']):
                match = False
                continue

        # Check version requirement
        if 'version' in requirements:
            if tags.get('version') != requirements['version']:
                match = False
                continue

        # Check architecture requirement
        if 'arch' in requirements:
            if tags.get('arch') != requirements['arch']:
                match = False
                continue

        # Check extensions requirement - all required extensions must be present
        if 'extensions' in requirements:
            required_exts = requirements['extensions']
            available_exts = tags.get('extensions', [])

            # Check if all required extensions are available
            for req_ext in required_exts:
                if req_ext not in available_exts:
                    match = False
                    break

            if not match:
                continue

        # If all checks passed, return this image
        if match:
            return image.get('image')

    # No matching image found
    return None


class FilterModule(object):
    """Ansible filter module for image selection"""

    def filters(self):
        return {
            'select_talos_image': select_talos_image
        }
