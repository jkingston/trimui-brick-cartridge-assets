"""Compact embedded glTF buffers without changing referenced bytes."""
import base64


def compact_buffers(document):
    # Extension-owned binary schemas are not assumed to follow core references.
    if document.get('extensions'):
        return
    references = []
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'bufferView' and isinstance(child, int):
                    references.append(value)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for key, value in document.items():
        if key not in ('buffers', 'bufferViews'):
            visit(value)
    views = document.get('bufferViews', [])
    used = sorted({r['bufferView'] for r in references})
    remap = {old:new for new,old in enumerate(used)}
    for reference in references:
        reference['bufferView'] = remap[reference['bufferView']]
    document['bufferViews'] = [views[i] for i in used]
    locations = []
    for view in document['bufferViews']:
        locations.append(view)
        compressed = view.get('extensions', {}).get('EXT_meshopt_compression')
        if compressed:
            locations.append(compressed)
    if not document['bufferViews']:
        document.pop('bufferViews')
    buffers = document.get('buffers', [])
    used_buffers = sorted({location['buffer'] for location in locations})
    for index in used_buffers:
        buffer = buffers[index]
        uri = buffer.get('uri')
        if not uri:
            # Meshopt logical output/fallback buffers have no stored payload.
            continue
        if not uri.startswith('data:') or ';base64,' not in uri:
            raise ValueError('Buffer compaction requires embedded data')
        raw = base64.b64decode(uri.split(',',1)[1], validate=True)
        selected = [location for location in locations if location['buffer'] == index]
        ranges = sorted((location.get('byteOffset',0), location.get('byteOffset',0)+location['byteLength']) for location in selected)
        merged = []
        for start,end in ranges:
            if not (0 <= start <= end <= len(raw)):
                raise ValueError('Buffer view lies outside stored payload')
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1],end)
            else:
                merged.append([start,end])
        result = bytearray()
        mapping = []
        for start,end in merged:
            # Preserve the original alignment of every overlapping subrange.
            result.extend(b'\0' * ((start - len(result)) % 4))
            mapping.append((start,end,len(result)))
            result.extend(raw[start:end])
        for location in selected:
            old = location.get('byteOffset',0)
            start,end,new = next(row for row in mapping if row[0] <= old and old+location['byteLength'] <= row[1])
            location['byteOffset'] = new+old-start
        buffer['byteLength'] = len(result)
        buffer['uri'] = 'data:application/octet-stream;base64,'+base64.b64encode(result).decode('ascii')
    buffer_remap = {old:new for new,old in enumerate(used_buffers)}
    for location in locations:
        location['buffer'] = buffer_remap[location['buffer']]
    document['buffers'] = [buffers[i] for i in used_buffers]
    if not document['buffers']:
        document.pop('buffers')
