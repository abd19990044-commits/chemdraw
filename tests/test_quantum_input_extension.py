import asyncio
from types import SimpleNamespace

import pytest

from gaussian_input import generate_gaussian_16_input
from geometry_normalizer import normalize_geometry_xyz
from quantum_validation import manual_input
from psi4_extension_v4 import generate_psi4_input_v4


WATER = "O 0 0 0\nH 0.7586 0 0.5043\nH -0.7586 0 0.5043"


def gaussian_data(**extra):
    data = dict(
        coords=WATER,
        name="water",
        charge=0,
        mult=1,
        cores=4,
        gauss_memory_mb=4000,
        gauss_disk_mb=20000,
        gauss_task="optfreq",
        gauss_method="B3LYP",
        gauss_basis="Def2TZVP",
        gauss_dispersion="GD3BJ",
        gauss_solvation="SMD",
        gauss_solvent="Water",
        gauss_accuracy="tight",
    )
    data.update(extra)
    return data


def test_gaussian_linux_structure_resources_and_route():
    text = generate_gaussian_16_input(gaussian_data())
    assert text.startswith("%NProcShared=4\n%Mem=4000MB\n%Chk=water.chk\n#p ")
    assert "B3LYP/Def2TZVP Opt Freq EmpiricalDispersion=GD3BJ" in text
    assert "SCRF=(SMD,Solvent=Water)" in text
    assert "MaxDisk=20000MB" in text
    assert "\n\nwater | B3LYP/Def2TZVP | optfreq\n\n0 1\n" in text
    assert text.endswith("\n\n")


def test_gaussian_tddft_nstates_and_open_shell():
    radical = "O 0 0 0\nH 0 0 1"
    text = generate_gaussian_16_input(
        gaussian_data(
            coords=radical,
            mult=2,
            gauss_task="td",
            gauss_method="CAM-B3LYP",
            gauss_dispersion="none",
            nroots=25,
        )
    )
    assert "TD=(NStates=25)" in text
    assert "\n0 2\n" in text


@pytest.mark.parametrize(
    "overrides",
    [
        dict(mult=2),
        dict(gauss_disk_mb=20001),
        dict(gauss_task="ts", gauss_method="CCSD(T)"),
        dict(gauss_method="wB97XD", gauss_dispersion="GD3BJ"),
        dict(gauss_method="MP2", gauss_solvation="SMD"),
        dict(gauss_basis="basis\n%Chk=wrong.chk"),
    ],
)
def test_gaussian_rejects_invalid_combinations(overrides):
    with pytest.raises(ValueError):
        generate_gaussian_16_input(gaussian_data(**overrides))


def test_manual_gaussview_preserved_and_disk_checked():
    original = "%chk=gv.chk\r\n#p B3LYP/6-31G(d) MaxDisk=20GB\r\n\r\nTitle\r\n\r\n0 1\r\nH 0 0 0\r\nH 0 0 0.74\r\n"
    # 20 GiB exceeds the requested 20000 MB ceiling.
    with pytest.raises(ValueError, match="MaxDisk"):
        manual_input(original, "gaussian")
    valid = original.replace("20GB", "19000MB")
    assert manual_input(valid, "gaussian") == valid.replace("\r\n", "\n")
    with pytest.raises(ValueError, match="route"):
        manual_input("H 0 0 0", "gaussian")
    with pytest.raises(ValueError, match="MaxDisk"):
        manual_input(valid.replace("19000MB", "20000.5MB"), "gaussian")


def test_manual_psi4_preserves_psithon_blocks_and_rejects_binary():
    raw = "molecule mol {\n  0 1\n  H 0 0 0\n  H 0 0 0.74\n}\nenergy('scf')\n"
    assert manual_input(raw, "psi4") == raw
    with pytest.raises(ValueError):
        manual_input(raw + "\x00", "psi4")


def test_xyz_rejects_extra_atoms_nan_and_unknown_element():
    for raw in ("1\nH 0 0 0\nHe 0 0 1", "H nan 0 0", "Xx 0 0 0"):
        with pytest.raises(ValueError):
            normalize_geometry_xyz(raw)
    assert normalize_geometry_xyz("1\n\nH 0 0 0").splitlines()[0].startswith("H")


def test_psi4_generator_checks_spin_basis_and_sanitizes_output_name():
    data = dict(
        coords=WATER,
        charge=0,
        mult=1,
        psi_family="dft",
        psi_method="b3lyp",
        psi_basis="def2-svp",
        psi_calc_type="sp",
        psi_output="molden",
        name="../bad'\nname",
    )
    out = generate_psi4_input_v4(data)
    assert "molden(wfn, 'bad_name.molden')" in out
    with pytest.raises(ValueError, match="inconsistent"):
        generate_psi4_input_v4(dict(data, mult=2))
    with pytest.raises(ValueError, match="basis"):
        generate_psi4_input_v4(dict(data, psi_basis="def2-svp\nimport os"))


def test_orca_correlated_preset_explicitly_requests_auxiliary_basis():
    import app

    core = app.core
    assert "AutoAux" in core.MP2_VARIANTS[1] and "AutoAux" in core.CCSD_VARIANTS[-1]
    text = core.generate_orca_6_input(
        dict(
            coords=WATER,
            charge=0,
            mult=1,
            cores=4,
            ram=2000,
            calc_type="sp",
            family="f_ccsd",
            theory="DLPNO-CCSD(T) AutoAux",
            basis="def2-TZVP",
            disp="none",
            ri_type="none",
            solv_model="none",
        )
    )
    assert "! DLPNO-CCSD(T) AutoAux def2-TZVP SP" in text
    with pytest.raises(ValueError, match="TD-DFT"):
        core.generate_orca_6_input(
            dict(
                coords=WATER,
                charge=0,
                mult=1,
                calc_type="tddft",
                family="f_mp2",
                theory="MP2",
                basis="def2-TZVP",
            )
        )


def test_psi4_cam_b3lyp_uses_canonical_spelling():
    import psi4_extension as ui
    from psi4_extension_v4 import apply_dispersion_alias

    assert ui.PSI4_DFT_METHODS["CAM-B3LYP"] == "cam-b3lyp"
    assert apply_dispersion_alias("cam-b3lyp", "d3bj") == "cam-b3lyp-d3bj2b"


class Message:
    def __init__(self, text=""):
        self.text = text
        self.documents = []
        self.replies = []
        self._raw = {}
        self.document = None

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def reply_document(self, document, caption):
        self.documents.append((document.name, document.getvalue().decode(), caption))


class Query:
    def __init__(self, data):
        self.data = data
        self.edits = []
        self.answers = 0

    async def answer(self):
        self.answers += 1

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def test_gaussian_wizard_from_geometry_to_download():
    import app

    core = app.core
    ud = dict(coords=WATER, name="water", state="waiting_for_engine")
    ctx = SimpleNamespace(user_data=ud)

    async def run():
        for button in (
            "engine_gaussian",
            "quantum_guided_gaussian",
            "g_task_optfreq",
            "g_method_B3LYP",
            "g_basis_Def2TZVP",
            "g_disp_GD3BJ",
            "g_solv_SMD",
            "g_solvent_Water",
            "g_accuracy_tight",
        ):
            q = Query(button)
            await core.callback_handler(SimpleNamespace(callback_query=q), ctx)
            assert q.answers == 1 and q.edits
        for value in ("0", "1", "4", "4000", "20000"):
            m = Message(value)
            await core.handle_text_input(SimpleNamespace(message=m), ctx)
        return m

    last = asyncio.run(run())
    assert last.documents[0][0] == "water_g16.gjf"
    assert "MaxDisk=20000MB" in last.documents[0][1]
    assert not ud


def test_manual_path_accepts_psithon_without_geometry():
    import app

    core = app.core
    ctx = SimpleNamespace(user_data={})

    async def run():
        first = Message("/manual")
        await core.handle_text_input(SimpleNamespace(message=first), ctx)
        assert first.replies
        q = Query("select_quantum_manual_psi4")
        await core.callback_handler(SimpleNamespace(callback_query=q), ctx)
        assert ctx.user_data["state"] == "manual_psi4_input"
        final = Message("molecule mol {\n0 1\nH 0 0 0\nH 0 0 0.74\n}\nenergy('scf')")
        await core.handle_text_input(SimpleNamespace(message=final), ctx)
        return final

    last = asyncio.run(run())
    assert last.documents[0][0] == "manual_input.dat"
    assert "energy('scf')" in last.documents[0][1]


def test_gaussview_upload_enters_manual_path():
    import app

    core = app.core
    source = b"%chk=water.chk\n#p B3LYP/Def2SVP MaxDisk=19000MB\n\nWater\n\n0 1\nH 0 0 0\nH 0 0 0.74\n\n"

    class Remote:
        async def download_as_bytes(self):
            return source

    class Bot:
        async def get_file(self, file_id):
            return Remote()

    message = Message()
    message._raw = {"document": {"file_size": len(source)}}
    message.document = SimpleNamespace(file_name="water.com", file_id="test")
    ctx = SimpleNamespace(user_data={}, bot=Bot())
    asyncio.run(core.handle_file(SimpleNamespace(message=message), ctx))
    assert message.documents[0][0] == "water.gjf"
    assert message.documents[0][1].encode() == source


def test_group_wizard_isolated_by_sender():
    import app

    core = app.core

    class Bot:
        async def send_message(self, chat_id, text, **kwargs):
            return {"message_id": 1}

    def packet(sender, text):
        return {
            "message": {
                "from": {"id": sender},
                "chat": {"id": -123},
                "message_id": sender,
                "text": text,
            }
        }

    async def run():
        await core._dispatch_update(Bot(), packet(11, "/xyz"))
        await core._dispatch_update(Bot(), packet(22, "/manual"))
        await core._dispatch_update(Bot(), packet(11, WATER))

    asyncio.run(run())
    assert core._state_cache["quantum:-123:11"]["coords"]
    assert "coords" not in core._state_cache.get("quantum:-123:22", {})


def test_start_xyz_engine_menu_and_psi4_guided_entry():
    import app

    core = app.core
    ctx = SimpleNamespace(user_data={})

    async def run():
        welcome = Message("/start")
        await core.start_command(SimpleNamespace(message=welcome), ctx)
        assert len(welcome.replies) == 1
        assert all(name in welcome.replies[0][0] for name in ("ORCA", "Psi4", "Gaussian"))
        await core.handle_text_input(SimpleNamespace(message=Message("/xyz")), ctx)
        coords = Message(WATER)
        await core.handle_text_input(SimpleNamespace(message=coords), ctx)
        buttons = coords.replies[0][1]["reply_markup"]["inline_keyboard"]
        values = {item["callback_data"] for row in buttons for item in row}
        assert {"engine_orca", "engine_psi4", "engine_gaussian"} <= values
        for button in ("engine_psi4", "quantum_guided_psi4"):
            q = Query(button)
            await core.callback_handler(SimpleNamespace(callback_query=q), ctx)
            assert q.edits
        q = Query("psicalc_sp")
        await core.callback_handler(SimpleNamespace(callback_query=q), ctx)
        assert ctx.user_data["psi_calc_type"] == "sp"

    asyncio.run(run())


def test_orca_wizard_skips_invalid_correlation_settings():
    import app

    core = app.core
    ud = {"engine": "orca", "coords": WATER, "calc_type": "sp", "family": "f_mp2"}
    ctx = SimpleNamespace(user_data=ud)

    async def run():
        q = Query("meth_RI-MP2 AutoAux")
        await core.callback_handler(SimpleNamespace(callback_query=q), ctx)
        assert "basis family" in q.edits[0][0]
        assert ud["ri_type"] == "none" and ud["disp"] == "none"
        ud.update(calc_type="tddft")
        q = Query("fam_f_mp2")
        await core.callback_handler(SimpleNamespace(callback_query=q), ctx)
        assert "DFT functional" in q.edits[0][0]
        ud.update(calc_type="opt", family="f_ccsd")
        q = Query("fam_f_ccsd")
        await core.callback_handler(SimpleNamespace(callback_query=q), ctx)
        assert "CCSD derivatives" in q.edits[0][0]

    asyncio.run(run())


def test_existing_webhook_stays_available_without_configured_secret(monkeypatch):
    import app

    sent = []

    class Bot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))
            return {"message_id": 1}

    monkeypatch.setattr(app.core, "_make_bot", lambda: Bot())
    monkeypatch.setattr(app.core, "run_coro_in_background", asyncio.run)
    monkeypatch.setattr(app.core, "ORCA_WEBHOOK_SECRET", "")
    client = app.app.test_client()
    payload = {"message": {"from": {"id": 42}, "chat": {"id": 42}, "message_id": 5, "text": "/start"}}
    assert client.post("/telegram", json=payload).status_code == 200
    assert sent and "Gaussian 16" in sent[0][1]
    assert client.get("/registerWebhook").status_code == 403


def test_compound_lookup_preview_failure_still_shows_engine_menu(monkeypatch):
    import app

    core = app.core
    water = WATER
    async def lookup(name):
        return water, b"bad preview", "<bad html>", None

    monkeypatch.setattr(core, "fetch_compound_async", lookup)
    ctx = SimpleNamespace(user_data={})
    events = []

    class Progress:
        async def delete(self):
            events.append("deleted")

    class BrokenPreviewMessage:
        text = "#water"

        async def reply_text(self, value, **kwargs):
            if kwargs.get("parse_mode") == "HTML":
                raise RuntimeError("Telegram rejected HTML")
            events.append(("text", value, kwargs))
            return Progress()

        async def reply_photo(self, **kwargs):
            raise RuntimeError("Telegram rejected photo")

    async def run():
        await core.handle_name(SimpleNamespace(message=BrokenPreviewMessage()), ctx)

    asyncio.run(run())
    assert ctx.user_data["coords"] == normalize_geometry_xyz(water)
    assert ctx.user_data["state"] == "waiting_for_engine"
    last = events[-1]
    assert last[0] == "text"
    callback_values = {
        button["callback_data"]
        for row in last[2]["reply_markup"]["inline_keyboard"]
        for button in row
    }
    assert {"engine_orca", "engine_psi4", "engine_gaussian"} <= callback_values
