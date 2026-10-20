import asyncio
from types import SimpleNamespace

import pytest

import gaussian_catalog as catalog
import gaussian_advanced as advanced
import gaussian_input as gaussian

WATER = "O 0 0 0\nH 0.7586 0 0.5043\nH -0.7586 0 0.5043"

def base_data(**kwargs):
    d = dict(coords=WATER, name="water", charge=0, mult=1,
             gauss_task="sp", gauss_method="B3LYP", gauss_basis="Def2TZVP",
             cores=4, gauss_memory_mb=4000, gauss_disk_mb=5000,
             gauss_dispersion="none", gauss_solvation="none", gauss_accuracy="standard")
    d.update(kwargs)
    return d

def test_catalog_is_broad_and_method_task_limited():
    assert len(set(catalog.METHODS.values())) >= 35
    assert len(set(catalog.BASIS.values())) >= 25
    assert "Def2QZVP" in catalog.BASIS.values()
    assert "aug-cc-pVTZ" in catalog.BASIS.values()
    assert gaussian.allowed_tasks("CCSD(T)") == {"Single point energy": "sp"}
    assert "td" not in gaussian.allowed_tasks("HF").values()

def test_scf_grid_and_population_are_integrated_once():
    inp = gaussian.generate_gaussian_16_input(base_data(
        gauss_scf="tightxqc", gauss_scfcycles="200", gauss_grid="superfine",
        gauss_pop="hirshfeld", gauss_symmetry="nosymm", gauss_stable="check"))
    assert "SCF=(Tight,XQC,MaxCycle=200)" in inp
    assert inp.count("SCF=") == 1
    assert "Integral=SuperFineGrid" in inp
    assert "Pop=Hirshfeld" in inp
    assert "NoSymm" in inp and "Stable" in inp

def test_optimization_advanced_does_not_duplicate_opt():
    inp = gaussian.generate_gaussian_16_input(base_data(
        gauss_task="optfreq", gauss_optconv="verytight", gauss_opthess="calcfc",
        gauss_optcycles="100", gauss_raman="noraman"))
    assert "Opt=(VeryTight,CalcFC,MaxCycles=100)" in inp
    assert "Freq=NoRaman" in inp
    assert inp.count(" Opt") == 1

def test_td_root_invalid_above_nstates():
    with pytest.raises(ValueError, match="Root must not exceed NStates"):
        gaussian.generate_gaussian_16_input(base_data(gauss_task="td", nroots=2, gauss_tdroot="3"))

def test_unknown_functionals_restricted_and_no_section_injection():
    with pytest.raises(ValueError, match="method"):
        gaussian.generate_gaussian_16_input(base_data(gauss_method="CustomUnknown"))
    with pytest.raises(ValueError):
        catalog.custom_keyword("B3LYP #p Force", "method")
    with pytest.raises(ValueError):
        catalog.custom_keyword("GenECP", "basis")
    inp = gaussian.generate_gaussian_16_input(base_data(gauss_method="CustomUnknown", gauss_custom_method="CustomUnknown"))
    assert "#p CustomUnknown/Def2TZVP" in inp

def test_existing_guided_defaults_not_perturbed():
    inp = gaussian.generate_gaussian_16_input(base_data(gauss_task="optfreq",gauss_method="B3LYP",
        gauss_accuracy="tight"))
    assert "SCF=(Tight,XQC)" in inp
    assert "Integral=UltraFine" in inp
    assert "Opt Freq" in inp


class Query:
    def __init__(self,data):
        self.data=data
        self.answers=0
        self.edits=[]
    async def answer(self):
        self.answers+=1
    async def edit_message_text(self,text,**kw):
        self.edits.append((text,kw))

class Message:
    def __init__(self,text):
        self.text=text
        self.replies=[]
        self.docs=[]
    async def reply_text(self,text,**kw):
        self.replies.append((text,kw))
    async def reply_document(self,document,caption):
        self.docs.append((document.name,document.getvalue().decode(),caption))

def test_telegram_method_and_basis_catalog_to_output():
    import app
    core=app.core
    state={"coords":WATER,"name":"water","state":"waiting_for_engine"}
    ctx=SimpleNamespace(user_data=state)
    async def click(data):
        q=Query(data)
        await core.callback_handler(SimpleNamespace(callback_query=q),ctx)
        assert q.answers==1 and q.edits, data
        return q.edits[-1]
    async def run():
        await click("engine_gaussian")
        await click("quantum_guided_gaussian")
        await click("g_task_sp")
        await click("gcat_method_family")
        await click("gcat_mfam_2")
        await click("gcat_mpick_1")  # PBE0 from hybrid family
        assert state["gauss_method"]=="PBE1PBE"
        await click("gcat_basis_family")
        await click("gcat_bfam_3")
        await click("gcat_bpick_5")  # def2-QZVP
        assert state["gauss_basis"]=="Def2QZVP"
        await click("g_disp_none")
        await click("g_solv_none")
        await click("g_accuracy_standard")
        await click("gadv_gate_defaults")
        for value in ("0","1","4","4000","5000"):
            m=Message(value)
            await core.handle_text_input(SimpleNamespace(message=m),ctx)
        return m
    m=asyncio.run(run())
    assert m.docs
    assert "PBE1PBE/Def2QZVP" in m.docs[0][1]
    assert state=={}
