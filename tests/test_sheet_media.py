from PIL import Image

from autopost.media import guess_type, to_jpeg
from autopost.models import Media
from autopost.sheet import SheetQueue


class FakeWorksheet:
    title = "Posts"

    def __init__(self, data):
        self.data = data
        self.col_count = 10
        self.updates = []
        self.batches = []

    def get(self, **kw):
        return self.data

    def add_cols(self, n):
        self.col_count += n

    def update(self, values, range_name, raw=True):
        self.updates.append((range_name, values))

    def batch_update(self, batch, raw=True):
        self.batches.append(batch)


def test_queue_adds_missing_output_columns_and_writes_cells():
    ws = FakeWorksheet([
        ["Post at", "Platforms", "Caption", "Media"],
        [46357.625, "fb", "hello", ""],
        ["", "", "", ""],
    ])
    q = SheetQueue(ws)
    q.load()
    assert ws.updates[0][0] == "E1:J1"  # 6 output columns appended after column D
    assert q.columns["status"] == 5 and q.columns["tiktok_result"] == 8
    rows = list(q.rows())
    assert rows[0].raw("post_at") == 46357.625 and rows[1].is_blank()
    q.write(rows[0], {"status": "posted", "facebook_result": "✅ x"})
    assert ws.batches[0] == [{"range": "E2", "values": [["posted"]]}, {"range": "F2", "values": [["✅ x"]]}]


def test_png_with_transparency_becomes_jpeg(tmp_path):
    src = tmp_path / "logo.png"
    Image.new("RGBA", (40, 30), (255, 0, 0, 0)).save(src)
    out = to_jpeg(Media(src, "image/png", src.stat().st_size, "logo.png"))
    with Image.open(out) as img:
        assert img.format == "JPEG" and img.size == (40, 30) and img.mode == "RGB"


def test_guess_type():
    assert guess_type("a.MOV") == "video/quicktime"
    assert guess_type("a.bin", "video/mp4; charset=x") == "video/mp4"
    assert guess_type("photo.jpg") == "image/jpeg"
