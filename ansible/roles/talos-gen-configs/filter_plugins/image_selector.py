"""
Custom Ansible filter for selecting Talos images based on requirements
"""


def select_talos_image(images_info, requirements):
    """
    Select the first image from images_info that matches all requirements.

    Args:
        images_info: List of dicts with 'name', 'image', and 'tags' keys
        requirements: Dict with required tag values (secureboot, version, extensions)

    Returns:
        String with the image URL, or None if no match found
    """
    for image in images_info:
        tags = image.get('tags', {})
        match = True

        # Check secureboot requirement
        if 'secureboot' in requirements:
            if tags.get('secureboot') != requirements['secureboot']:
                match = False
                continue

        # Check version requirement
        if 'version' in requirements:
            if tags.get('version') != requirements['version']:
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
