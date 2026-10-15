# For tests/test_config.py, next to test_eigen_profile. NAME, LIBRARY and
# NAME_ROOT as in the profile's module.

def test_NAME_profile(tmp_path, monkeypatch):
    monkeypatch.delenv('NAME_ROOT', raising=False)
    try:
        cfg = Config(Options(cwd=tmp_path, profiles=['NAME']), backend=False)
    except ConfigError as e:
        pytest.skip(str(e))   # the library is not installed here
    assert cfg.preludes[-1].name == 'cpp_repl_NAME.h'
    assert cfg.profiles[0].banner.startswith('LIBRARY ')
    assert all(lib.is_file() for lib in cfg.libs)
    flags = cfg.compiler_flags
    assert not any(a in ('-I', '-isystem') and b in ('/usr/include', '/usr/local/include')
                   for a, b in zip(flags, flags[1:]))


def test_NAME_profile_root_variable(tmp_path, monkeypatch):
    monkeypatch.setenv('NAME_ROOT', str(tmp_path))
    with pytest.raises(ConfigError, match='NAME_ROOT'):
        Config(Options(cwd=tmp_path, profiles=['NAME']), backend=False)
