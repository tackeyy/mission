def present(minor, fee):
    total = minor + fee
    return {'minor_total': total, 'display_major': total / 100}
