"""Bounded XML metadata updates that preserve unrelated properties."""

import re
from xml.etree import ElementTree as ET

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "Iptc4xmpCore": "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/",
}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)
ALIASES = {"artist": "creator", "keywords": "subject", "copyright": "rights"}


def parse_xml(data):
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("XML metadata exceeds 16 MiB limit")
    # Metadata does not need external entities or DTDs.
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("DTD/entity declarations are not supported in metadata")
    return ET.fromstring(data)


def xmp_tag(key):
    prefix, name = key.split(":", 1) if ":" in key else ("dc", key)
    if prefix not in NS or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):
        raise ValueError(f"Unsupported XMP property: {key}")
    if prefix == "dc":
        name = ALIASES.get(name.lower(), name.lower())
        if name not in {
            "title",
            "creator",
            "description",
            "subject",
            "rights",
            "date",
            "type",
            "format",
            "identifier",
            "source",
            "language",
            "relation",
            "coverage",
            "publisher",
            "contributor",
        }:
            raise ValueError(f"Unsupported Dublin Core property: {key}; use an explicit supported namespace")
    return f"{{{NS[prefix]}}}{name}"


def merge_xmp(existing, fields):
    root = parse_xml(existing) if existing else ET.Element(f"{{{NS['x']}}}xmpmeta")
    rdf = root.find(f".//{{{NS['rdf']}}}RDF")
    if rdf is None:
        if existing:
            raise ValueError("Existing XML is not an XMP RDF packet")
        rdf = ET.SubElement(root, f"{{{NS['rdf']}}}RDF")
    descriptions = rdf.findall(f"{{{NS['rdf']}}}Description")
    if not descriptions:
        descriptions = [ET.SubElement(rdf, f"{{{NS['rdf']}}}Description", {f"{{{NS['rdf']}}}about": ""})]
    updated = set()
    for field in fields:
        if isinstance(field.value, list) and not field.value:
            raise ValueError("Empty XMP arrays are not supported; deletion must be explicit")
        tag = xmp_tag(field.key)
        if tag in updated:
            raise ValueError(f"Duplicate XMP property/alias: {field.key}")
        updated.add(tag)
        values = field.value if isinstance(field.value, list) else [field.value]
        if any(not isinstance(v, (str, int, float)) or isinstance(v, bool) for v in values):
            raise ValueError("Native XMP values must be text or numbers; use ExifTool for structured properties")
        for description in descriptions:
            description.attrib.pop(tag, None)
            for child in list(description):
                if child.tag == tag:
                    description.remove(child)
        prop = ET.SubElement(descriptions[0], tag)
        values = field.value if isinstance(field.value, list) else [field.value]
        if tag in {f"{{{NS['dc']}}}{n}" for n in ("title", "description", "rights")}:
            if len(values) != 1:
                raise ValueError("Native language-alternative properties accept one default-language value")
            group = ET.SubElement(prop, f"{{{NS['rdf']}}}Alt")
            ET.SubElement(
                group, f"{{{NS['rdf']}}}li", {"{http://www.w3.org/XML/1998/namespace}lang": "x-default"}
            ).text = str(values[0])
        elif tag in {
            f"{{{NS['dc']}}}{n}"
            for n in ("creator", "date", "contributor", "language", "publisher", "relation", "subject", "type")
        } or isinstance(field.value, list):
            sequence = tag in {f"{{{NS['dc']}}}creator", f"{{{NS['dc']}}}date"}
            group = ET.SubElement(prop, f"{{{NS['rdf']}}}{'Seq' if sequence else 'Bag'}")
            for value in values:
                ET.SubElement(group, f"{{{NS['rdf']}}}li").text = str(value)
        else:
            prop.text = str(field.value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def read_xmp(data):
    root = parse_xml(data)
    result = {}
    prefixes = {uri: prefix for prefix, uri in NS.items()}
    for description in root.iter(f"{{{NS['rdf']}}}Description"):
        for prop in list(description):
            uri, _, name = prop.tag[1:].partition("}")
            key = f"{prefixes.get(uri, uri)}:{name}"
            items = prop.findall(f".//{{{NS['rdf']}}}li")
            values = [item.text or "" for item in items]
            result[key] = values if len(values) > 1 else values[0] if values else prop.text or ""
    return result
