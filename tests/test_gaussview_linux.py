from gaussview_linux import convert_gaussview_to_linux, decode_gaussview_text
from quantum_validation import manual_input
import pytest


def test_windows_link0_paths_are_linux_safe_without_modifying_chemistry():
    source = (
        "%NProcShared=12\r\n%Mem=28GB\r\n"
        "%Chk=C:\\Users\\Alice\\Documents\\research\\naproxen.chk\r\n"
        "%OldChk=D:\\Gaussian\\prior.chk\r\n"
        "%RWF=C:\\Gaussian\\Scratch\\run.rwf,20GB\r\n"
        "#p B3LYP/Def2TZVP Opt Freq SCRF=(SMD,Solvent=Water)\r\n\r\n"
        "Original job title\r\n\r\n0 1\r\nO 0 0 0\r\nH 0 0 1\r\n\r\n"
    )
    report = convert_gaussview_to_linux(source)
    out = manual_input(report.content, "gaussian")
    assert "%Chk=naproxen.chk\n" in out
    assert "%OldChk=prior.chk\n" in out
    assert "%RWF=run.rwf,20GB\n" in out
    assert "%Mem=28GB\n" in out and "%NProcShared=12\n" in out
    assert "#p B3LYP/Def2TZVP Opt Freq SCRF=(SMD,Solvent=Water)\n" in out
    assert "Original job title\n\n0 1\nO 0 0 0\nH 0 0 1\n\n" in out
    assert report.required_files == ("prior.chk",)


def test_link1_and_external_basis_paths_are_preserved():
    src = (
        "%Chk=C:\\Calc\\one.chk\n#p HF/6-31G(d) SP\n\nfirst\n\n0 1\nH 0 0 0\n\n"
        "--Link1--\n"
        "%OldChk=C:\\Calc\\one.chk\n%Chk=C:\\Calc\\two.chk\n"
        "#p B3LYP/GenECP Geom=Check Guess=Read\n\nsecond\n\n0 1\nH 0 0 0\n\n"
        "@C:\\basis\\custom.gbs/N\n"
    )
    result = convert_gaussview_to_linux(src)
    assert "--Link1--" in result.content
    assert result.content.count("%Chk=") == 2
    assert "%OldChk=one.chk" in result.content
    assert "@custom.gbs/N" in result.content
    assert "one.chk" in result.required_files
    assert "custom.gbs" in result.required_files


def test_non_windows_gaussian_is_not_changed():
    original = "%chk=gv.chk\r\n#p B3LYP/6-31G(d) MaxDisk=19000MB\r\n\r\nTitle\r\n\r\n0 1\r\nH 0 0 0\r\nH 0 0 0.74\r\n"
    assert convert_gaussview_to_linux(original).content == original.replace("\r\n", "\n")


def test_cp1252_utf16_and_colliding_paths():
    assert "Café" in decode_gaussview_text("Café".encode("cp1252"))
    assert "%Chk" in decode_gaussview_text("%Chk=x.chk".encode("utf-16"))
    with pytest.raises(ValueError, match="same Linux filename"):
        convert_gaussview_to_linux(
            "%Chk=C:\\foo\\job.chk\n%OldChk=D:\\bar\\job.chk\n"
            "#p HF/6-31G(d)\n\nx\n\n0 1\nH 0 0 0\n\n"
        )
