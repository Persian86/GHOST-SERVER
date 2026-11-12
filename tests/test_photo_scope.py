import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ghost_server import store,photos

def test_photo_scan_filters_descendants_not_ancestors(tmp_path):
    root=tmp_path/'#archive'/'photos';root.mkdir(parents=True)
    (root/'family.jpg').write_bytes(b'test listing')
    (root/'@eaDir').mkdir();(root/'@eaDir'/'thumbnail.jpg').write_bytes(b'test listing')
    store.init(tmp_path/'photos.db');store.put('photo_folder',str(root))
    photos._scan['at']=0
    assert [p.name for p in photos.files()]==['family.jpg']
