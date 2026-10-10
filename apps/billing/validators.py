import re


def normalize_vat_id(value: str, country: str = "", *, add_country_prefix: bool = False) -> str:
    normalized = re.sub(r"[\s.\-_/]", "", value or "").upper()
    country = (country or "").upper()
    if add_country_prefix and country and normalized and not normalized.startswith(country):
        if len(normalized) >= 2 and normalized[:2].isalpha():
            return normalized
        return f"{country}{normalized}"
    return normalized


def is_valid_polish_nip(vat_id: str) -> bool:
    normalized = normalize_vat_id(vat_id)
    if not re.fullmatch(r"PL\d{10}", normalized):
        return False
    number = normalized[2:]
    weights = [6, 5, 7, 2, 3, 4, 5, 6, 7]
    checksum = sum(int(number[index]) * weights[index] for index in range(9)) % 11
    return checksum != 10 and checksum == int(number[9])
