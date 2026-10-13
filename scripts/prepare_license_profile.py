"""Validate and stage the public licensing configuration for a release build."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from octop.infra.licensing.service import PRODUCT, LicenseProfile, decode_base64


def verify_server(profile: LicenseProfile, *, transport: httpx.BaseTransport | None = None) -> None:
    """Check availability against an already trusted, administrator-exported key."""
    try:
        profile.validate_release()
    except ValueError:
        raise ValueError("发布配置无效，请重新从可信的服务器管理端导出。") from None
    if profile.mode != "server" or urlsplit(profile.server_url).scheme != "https":
        raise ValueError("在线发布检查需要使用 HTTPS 的服务器授权配置。")
    pinned_key = decode_base64(profile.public_key)
    base_url = profile.server_url.rstrip("/")
    with httpx.Client(timeout=10, follow_redirects=False, transport=transport) as client:

        def read_json(path: str) -> dict[str, object]:
            try:
                response = client.get(base_url + path)
            except httpx.HTTPError:
                raise ValueError(f"激活服务 {path} 无法连接，请检查 HTTPS 服务。") from None
            if response.is_redirect:
                raise ValueError(f"激活服务 {path} 返回重定向，发布检查已停止。")
            if response.status_code != 200:
                raise ValueError(f"激活服务 {path} 未就绪（HTTP {response.status_code}）。")
            if (
                response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise ValueError(f"激活服务 {path} 未返回 JSON，请检查网站反向代理。")
            try:
                payload = response.json()
            except ValueError:
                raise ValueError(f"激活服务 {path} 返回无效 JSON。") from None
            if not isinstance(payload, dict):
                raise ValueError(f"激活服务 {path} 返回的 JSON 格式不正确。")
            return payload

        ready = read_json("/ready")
        if ready.get("status") != "ready":
            raise ValueError("激活服务尚未就绪，发布检查已停止。")
        if ready.get("public_key_sha256") != hashlib.sha256(pinned_key).hexdigest():
            raise ValueError("激活服务就绪检查的公钥指纹与预固定公钥不一致。")
        public = read_json("/api/v1/public-key")
        if public.get("product") != PRODUCT or public.get("algorithm") != "Ed25519":
            raise ValueError("激活服务产品或签名算法不匹配。")
        if profile.cloud_accounts:
            account = read_json("/api/v2/auth/status")
            if account.get("protocol") != 2 or account.get("offline_seconds") != 72 * 3600:
                raise ValueError("云账号服务协议或离线租约策略不匹配。")
        try:
            raw_key = public.get("public_key")
            if not isinstance(raw_key, str) or decode_base64(raw_key) != pinned_key:
                raise ValueError
        except ValueError:
            raise ValueError("激活服务公钥与可信发布配置中的预固定公钥不一致。") from None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--local-trial-days", type=int, choices=range(1, 366))
    parser.add_argument("--verify-server", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.verify_server and args.local_trial_days is not None:
        parser.error("--verify-server 不能与 --local-trial-days 一起使用。")
    try:
        profile = (
            LicenseProfile(mode="local_trial", trial_days=args.local_trial_days)
            if args.local_trial_days is not None
            else LicenseProfile.model_validate_json(args.input.read_text(encoding="utf-8"))
        )
        profile.validate_release()
    except (OSError, ValueError):
        parser.error("发布配置无效，请检查输入文件并从可信的服务器管理端重新导出。")
    if args.verify_server:
        try:
            verify_server(profile)
        except ValueError as exc:
            parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(profile.model_dump_json(indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
