import pytest
from ajcine.parsing import parse_filename, phonetic_key, norm
from ajcine.parsing.languages import detect


def test_spec_example_series():
    p = parse_filename("Breaking.Bad.S02E05.1080p.WEB-DL.HEVC.DD5.1.English.mkv")
    assert (p.title, p.content_type, p.season, p.episode) == ("Breaking Bad", "series", 2, 5)
    assert (p.resolution, p.video_codec, p.audio_codec, p.languages) == ("1080p", "HEVC", "DD5.1", ["en"])
    assert p.source == "WEB-DL"


def test_spec_example_movie():
    p = parse_filename("Premalu.2024.Malayalam.1080p.WEB-DL.mkv")
    assert (p.title, p.year, p.content_type, p.languages, p.resolution) == ("Premalu", 2024, "movie", ["ml"], "1080p")


def test_channel_prefix_and_dual_audio():
    p = parse_filename("@MovieHub - Inception (2010) Hindi Dual Audio 720p BluRay x264 ESub.mkv")
    assert p.title == "Inception" and p.year == 2010 and p.quality == "720p"
    assert p.languages == ["hi"] and p.multi_audio and p.subtitles == ["en"]


def test_title_that_looks_like_a_language():
    p = parse_filename("Hindi Medium 2017 1080p WEBRip x265 AAC-RARBG.mkv")
    assert p.title == "Hindi Medium" and p.year == 2017 and p.release_group == "RARBG"


def test_multi_language_list():
    p = parse_filename("Kantara (2022) [Kannada + Hindi + Tamil] 1080p WEB-DL.mkv")
    assert p.languages == ["kn", "hi", "ta"]


def test_season_pack_and_complete_series():
    p = parse_filename("The.Last.of.Us.S01.Complete.2160p.AMZN.WEB-DL.DDP5.1.Atmos.HDR10+.HEVC-FLUX.mkv")
    assert p.content_type == "series" and p.season == 1 and p.is_season_pack and p.episode is None
    assert p.quality == "2160p" and "HDR10+" in p.hdr and p.release_group == "FLUX"
    c = parse_filename("Game of Thrones Complete Series 1080p BluRay.mkv")
    assert c.is_complete_series and c.title == "Game of Thrones"


def test_episode_range_and_nxn():
    p = parse_filename("Money.Heist.S03E01-E03.720p.NF.WEB-DL.Hin-Eng.x264.mkv")
    assert (p.season, p.episode, p.episode_end) == (3, 1, 3) and p.languages == ["hi", "en"]
    q = parse_filename("Mr.Robot.1x03.720p.HDTV.x264.mkv")
    assert (q.title, q.season, q.episode) == ("Mr Robot", 1, 3)


def test_cam_detection_and_resolution_not_confused():
    p = parse_filename("Avengers Endgame 2019 HDCAM Telugu.mp4")
    assert p.is_cam and p.languages == ["te"]
    assert parse_filename("Movie.2020.1080p.WEB-DL.DTS.mkv").is_cam is False


def test_year_inside_title():
    p = parse_filename("Blade Runner 2049 2017 2160p UHD BluRay REMUX HDR DV TrueHD Atmos 7.1.mkv")
    assert p.title == "Blade Runner 2049" and p.year == 2017 and p.quality == "2160p"


def test_empty_and_garbage():
    assert parse_filename(None).title == ""
    assert parse_filename("   ").content_type == "unknown"


def test_phonetic_variants_match():
    assert phonetic_key("Premaalu") == phonetic_key("Premalu")
    assert norm("Spider-Man: No Way Home") == "spider man no way home"


def test_subtitle_languages_are_not_audio():
    d = detect("Movie 2020 English Subs Hindi")
    assert d["subs"] == ["en"] and d["audio"] == ["hi"]
