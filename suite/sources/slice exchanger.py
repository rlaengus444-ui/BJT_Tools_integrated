import os
import re
import shutil
import threading
import traceback
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except Exception:
    DND_AVAILABLE = False


IMAGE_EXTS = {
    ".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg"
}


MODE_BITS = {
    "1": 1,
    "L": 8,
    "P": 8,
    "LA": 16,
    "RGB": 24,
    "RGBA": 32,
    "CMYK": 32,
    "YCbCr": 24,
    "I": 32,
    "F": 32,
    "I;16": 16,
    "I;16L": 16,
    "I;16B": 16,
    "I;16N": 16,
}


def parse_drop_path(data: str) -> str:
    data = data.strip()

    if data.startswith("{"):
        match = re.match(r"\{(.+?)\}", data)
        if match:
            return match.group(1)

    return data.split()[0]


def is_numeric_image_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in IMAGE_EXTS:
        return False
    return path.stem.isdigit()


def build_number_file_map(folder: Path):
    result = {}
    duplicate = {}

    for p in folder.iterdir():
        if is_numeric_image_file(p):
            num = int(p.stem)

            if num in result:
                duplicate.setdefault(num, []).append(result[num])
                duplicate[num].append(p)
            else:
                result[num] = p

    if duplicate:
        msg = ["같은 숫자 이름의 이미지가 중복됩니다."]
        for num, files in duplicate.items():
            msg.append(f"  번호 {num}: " + ", ".join(str(x.name) for x in files))
        raise RuntimeError("\n".join(msg))

    return result


def get_image_signature(path: Path):
    try:
        with Image.open(path) as im:
            im.load()

            mode = im.mode
            bits = MODE_BITS.get(mode)

            if bits is None:
                bits = im.info.get("bits", None)

            return {
                "ext": path.suffix.lower(),
                "format": im.format,
                "size": im.size,
                "mode": mode,
                "bands": im.getbands(),
                "bits": bits,
            }

    except Exception as e:
        raise RuntimeError(f"이미지 정보를 읽을 수 없습니다: {path}\n{e}")


def signature_to_text(sig):
    return (
        f"ext={sig['ext']}, "
        f"format={sig['format']}, "
        f"size={sig['size'][0]}x{sig['size'][1]}, "
        f"mode={sig['mode']}, "
        f"bands={sig['bands']}, "
        f"bits={sig['bits']}"
    )


def check_missing_numbers(number_map, start_num, end_num, folder_name):
    missing = []

    for n in range(start_num, end_num + 1):
        if n not in number_map:
            missing.append(n)

    if missing:
        preview = ", ".join(map(str, missing[:50]))
        if len(missing) > 50:
            preview += f" ... 외 {len(missing) - 50}개"

        raise RuntimeError(
            f"{folder_name}에서 지정 범위 숫자 파일이 누락되었습니다.\n"
            f"누락 번호: {preview}"
        )


def check_folder_continuity_all(number_map, folder_name):
    if not number_map:
        raise RuntimeError(f"{folder_name}에 숫자 이미지 파일이 없습니다.")

    nums = sorted(number_map.keys())
    min_n = nums[0]
    max_n = nums[-1]

    missing = [n for n in range(min_n, max_n + 1) if n not in number_map]

    if missing:
        preview = ", ".join(map(str, missing[:50]))
        if len(missing) > 50:
            preview += f" ... 외 {len(missing) - 50}개"

        raise RuntimeError(
            f"{folder_name} 전체 숫자 파일에 빠진 번호가 있습니다.\n"
            f"전체 범위: {min_n} ~ {max_n}\n"
            f"누락 번호: {preview}"
        )


def check_folder_format_consistency(number_map, folder_name, log):
    nums = sorted(number_map.keys())

    base_num = nums[0]
    base_path = number_map[base_num]
    base_sig = get_image_signature(base_path)

    log(f"[최종검사] {folder_name} 기준 파일: {base_path.name}")
    log(f"[최종검사] 기준 형식: {signature_to_text(base_sig)}")

    bad = []

    for n in nums:
        path = number_map[n]
        sig = get_image_signature(path)

        if sig != base_sig:
            bad.append((n, path.name, sig))

    if bad:
        msg = [
            f"{folder_name} 전체 이미지 형식이 일관되지 않습니다.",
            f"기준 파일: {base_path.name}",
            f"기준 형식: {signature_to_text(base_sig)}",
            "",
            "문제 파일 예시:"
        ]

        for n, name, sig in bad[:30]:
            msg.append(f"  {name}: {signature_to_text(sig)}")

        if len(bad) > 30:
            msg.append(f"  ... 외 {len(bad) - 30}개")

        raise RuntimeError("\n".join(msg))


def compare_pair_signatures(a_path: Path, b_path: Path):
    a_sig = get_image_signature(a_path)
    b_sig = get_image_signature(b_path)

    if a_sig != b_sig:
        raise RuntimeError(
            "대응 파일의 이미지 형식이 서로 다릅니다.\n"
            f"A 파일: {a_path.name}\n"
            f"A 형식: {signature_to_text(a_sig)}\n"
            f"B 파일: {b_path.name}\n"
            f"B 형식: {signature_to_text(b_sig)}"
        )


def safe_replace_images(
    a_folder,
    b_folder,
    a_start,
    a_end,
    b_start,
    b_end,
    log
):
    a_folder = Path(a_folder)
    b_folder = Path(b_folder)

    if not a_folder.exists() or not a_folder.is_dir():
        raise RuntimeError(f"A 폴더가 올바르지 않습니다: {a_folder}")

    if not b_folder.exists() or not b_folder.is_dir():
        raise RuntimeError(f"B 폴더가 올바르지 않습니다: {b_folder}")

    if a_start > a_end:
        raise RuntimeError("A 시작 번호가 A 끝 번호보다 큽니다.")

    if b_start > b_end:
        raise RuntimeError("B 시작 번호가 B 끝 번호보다 큽니다.")

    a_count = a_end - a_start + 1
    b_count = b_end - b_start + 1

    if a_count != b_count:
        raise RuntimeError(
            "A 범위와 B 범위의 파일 개수가 다릅니다.\n"
            f"A: {a_start}~{a_end} = {a_count}개\n"
            f"B: {b_start}~{b_end} = {b_count}개"
        )

    log("========== 작업 시작 ==========")
    log(f"A 폴더: {a_folder}")
    log(f"B 폴더: {b_folder}")
    log(f"A 교체 범위: {a_start} ~ {a_end}")
    log(f"B 소스 범위: {b_start} ~ {b_end}")
    log(f"총 교체 개수: {a_count}개")
    log("")

    log("[1/6] 숫자 이미지 파일 목록 수집 중...")
    a_map = build_number_file_map(a_folder)
    b_map = build_number_file_map(b_folder)

    log(f"A 폴더 숫자 이미지 수: {len(a_map)}")
    log(f"B 폴더 숫자 이미지 수: {len(b_map)}")
    log("")

    log("[2/6] 지정 범위 누락 파일 검사 중...")
    check_missing_numbers(a_map, a_start, a_end, "A 폴더")
    check_missing_numbers(b_map, b_start, b_end, "B 폴더")
    log("지정 범위 누락 없음.")
    log("")

    log("[3/6] 대응 파일 확장자/해상도/비트수/모드 검사 중...")

    pairs = []

    for i in range(a_count):
        a_num = a_start + i
        b_num = b_start + i

        a_path = a_map[a_num]
        b_path = b_map[b_num]

        compare_pair_signatures(a_path, b_path)
        pairs.append((a_num, b_num, a_path, b_path))

        if (i + 1) % 100 == 0 or (i + 1) == a_count:
            log(f"  검사 완료: {i + 1} / {a_count}")

    log("대응 파일 형식 검사 통과.")
    log("")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_folder = a_folder / f"_backup_before_replace_{timestamp}"

    log("[4/6] A 폴더 원본 백업 중...")
    backup_folder.mkdir(exist_ok=False)

    try:
        for i, (_, _, a_path, _) in enumerate(pairs, start=1):
            shutil.copy2(a_path, backup_folder / a_path.name)

            if i % 100 == 0 or i == a_count:
                log(f"  백업 완료: {i} / {a_count}")

        log(f"백업 폴더 생성: {backup_folder}")
        log("")

        log("[5/6] B 이미지 내용으로 A 파일 덮어쓰기 중...")

        for i, (_, _, a_path, b_path) in enumerate(pairs, start=1):
            shutil.copy2(b_path, a_path)

            if i % 100 == 0 or i == a_count:
                log(f"  교체 완료: {i} / {a_count}")

        log("덮어쓰기 완료.")
        log("")

        log("[6/6] 최종 무결성 검사 중...")
        a_map_after = build_number_file_map(a_folder)

        check_folder_continuity_all(a_map_after, "A 폴더")
        log("A 폴더 전체 숫자 연속성 검사 통과.")

        check_folder_format_consistency(a_map_after, "A 폴더", log)
        log("A 폴더 전체 이미지 형식 일관성 검사 통과.")
        log("")

        log("========== 작업 성공 ==========")
        log("최종 확인까지 모두 통과했습니다.")
        log(f"백업 폴더는 보존됩니다: {backup_folder}")

    except Exception as e:
        log("")
        log("!!!!!! 최종 검사 또는 교체 중 오류 발생 !!!!!!")
        log(str(e))
        log("")
        log("[자동복구] 백업 파일로 A 폴더를 이전 상태로 복구합니다...")

        restore_errors = []

        for _, _, a_path, _ in pairs:
            backup_path = backup_folder / a_path.name

            try:
                if backup_path.exists():
                    shutil.copy2(backup_path, a_path)
            except Exception as restore_e:
                restore_errors.append((a_path.name, str(restore_e)))

        if restore_errors:
            log("[자동복구 실패 항목]")
            for name, err in restore_errors:
                log(f"  {name}: {err}")

            raise RuntimeError(
                "작업 중 오류가 발생했고, 일부 파일 복구에도 실패했습니다.\n"
                "백업 폴더를 직접 확인하세요.\n"
                f"백업 폴더: {backup_folder}"
            )

        log("[자동복구 완료] A 폴더를 작업 전 상태로 되돌렸습니다.")
        log(f"백업 폴더: {backup_folder}")

        raise


class ImageReplaceApp:
    def __init__(self, root):
        self.root = root
        self.root.title("슬라이싱 이미지 범위 교체 도구")
        self.root.geometry("920x720")

        self.is_running = False

        self.create_widgets()

    def create_widgets(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        folder_frame = ttk.LabelFrame(main, text="폴더 선택")
        folder_frame.pack(fill="x", pady=5)

        self.a_folder_var = tk.StringVar()
        self.b_folder_var = tk.StringVar()

        self.create_folder_row(
            folder_frame,
            "A 폴더 / 교체될 폴더",
            self.a_folder_var,
            0
        )

        self.create_folder_row(
            folder_frame,
            "B 폴더 / 소스 폴더",
            self.b_folder_var,
            1
        )

        range_frame = ttk.LabelFrame(main, text="교체 범위")
        range_frame.pack(fill="x", pady=5)

        self.a_start_var = tk.StringVar()
        self.a_end_var = tk.StringVar()
        self.b_start_var = tk.StringVar()
        self.b_end_var = tk.StringVar()

        ttk.Label(range_frame, text="A 시작 번호").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        ttk.Entry(range_frame, textvariable=self.a_start_var, width=12).grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(range_frame, text="A 끝 번호").grid(row=0, column=2, padx=5, pady=5, sticky="w")
        ttk.Entry(range_frame, textvariable=self.a_end_var, width=12).grid(row=0, column=3, padx=5, pady=5)

        ttk.Label(range_frame, text="B 시작 번호").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        ttk.Entry(range_frame, textvariable=self.b_start_var, width=12).grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(range_frame, text="B 끝 번호").grid(row=1, column=2, padx=5, pady=5, sticky="w")
        ttk.Entry(range_frame, textvariable=self.b_end_var, width=12).grid(row=1, column=3, padx=5, pady=5)

        info_text = (
            "동작 방식: A 폴더의 지정 번호 파일명을 유지하고, "
            "B 폴더의 지정 번호 이미지 내용으로 덮어씁니다.\n"
            "실행 전: A/B 대응 파일의 확장자, 해상도, 모드, 비트수 검사\n"
            "실행 후: A 폴더 전체 숫자 누락 여부 및 전체 이미지 형식 일관성 검사\n"
            "최종 검사 실패 시: 백업 파일로 자동 복구"
        )

        info = ttk.Label(main, text=info_text)
        info.pack(fill="x", pady=5)

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=5)

        self.run_btn = ttk.Button(
            btn_frame,
            text="검사 후 교체 실행",
            command=self.start_work
        )
        self.run_btn.pack(side="left", padx=5)

        clear_btn = ttk.Button(
            btn_frame,
            text="로그 지우기",
            command=self.clear_log
        )
        clear_btn.pack(side="left", padx=5)

        log_frame = ttk.LabelFrame(main, text="로그")
        log_frame.pack(fill="both", expand=True, pady=5)

        self.log_text = tk.Text(log_frame, height=24, wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

    def create_folder_row(self, parent, label, var, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=5, pady=5)

        entry = ttk.Entry(parent, textvariable=var, width=90)
        entry.grid(row=row, column=1, sticky="ew", padx=5, pady=5)

        btn = ttk.Button(
            parent,
            text="찾기",
            command=lambda: self.browse_folder(var)
        )
        btn.grid(row=row, column=2, padx=5, pady=5)

        parent.columnconfigure(1, weight=1)

        if DND_AVAILABLE:
            entry.drop_target_register(DND_FILES)
            entry.dnd_bind("<<Drop>>", lambda e, v=var: self.on_drop(e, v))

    def browse_folder(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def on_drop(self, event, var):
        path = parse_drop_path(event.data)

        if os.path.isdir(path):
            var.set(path)
        else:
            messagebox.showwarning("경고", "폴더만 드래그앤드롭할 수 있습니다.")

    def log(self, msg):
        def append():
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")

        self.root.after(0, append)

    def clear_log(self):
        self.log_text.delete("1.0", "end")

    def parse_int(self, value, name):
        try:
            return int(value)
        except Exception:
            raise RuntimeError(f"{name}은 숫자로 입력해야 합니다.")

    def start_work(self):
        if self.is_running:
            messagebox.showinfo("알림", "이미 작업이 실행 중입니다.")
            return

        try:
            a_folder = self.a_folder_var.get().strip()
            b_folder = self.b_folder_var.get().strip()

            a_start = self.parse_int(self.a_start_var.get().strip(), "A 시작 번호")
            a_end = self.parse_int(self.a_end_var.get().strip(), "A 끝 번호")
            b_start = self.parse_int(self.b_start_var.get().strip(), "B 시작 번호")
            b_end = self.parse_int(self.b_end_var.get().strip(), "B 끝 번호")

            if not a_folder or not b_folder:
                raise RuntimeError("A 폴더와 B 폴더를 모두 지정해야 합니다.")

        except Exception as e:
            messagebox.showerror("입력 오류", str(e))
            return

        confirm = messagebox.askyesno(
            "확인",
            "검사 후 문제가 없으면 A 폴더의 파일을 실제로 덮어씁니다.\n"
            "작업 전 A 폴더의 교체 대상 파일은 자동 백업됩니다.\n"
            "최종 검사에서 문제가 생기면 자동 복구를 시도합니다.\n\n"
            "진행할까요?"
        )

        if not confirm:
            return

        self.is_running = True
        self.run_btn.configure(state="disabled")

        t = threading.Thread(
            target=self.worker,
            args=(a_folder, b_folder, a_start, a_end, b_start, b_end),
            daemon=True
        )
        t.start()

    def worker(self, a_folder, b_folder, a_start, a_end, b_start, b_end):
        try:
            safe_replace_images(
                a_folder=a_folder,
                b_folder=b_folder,
                a_start=a_start,
                a_end=a_end,
                b_start=b_start,
                b_end=b_end,
                log=self.log
            )

            self.root.after(
                0,
                lambda: messagebox.showinfo("완료", "작업이 정상 완료되었습니다.")
            )

        except Exception:
            err = traceback.format_exc()

            self.log("")
            self.log("========== 오류 상세 ==========")
            self.log(err)

            self.root.after(
                0,
                lambda: messagebox.showerror(
                    "오류",
                    "작업이 실패했습니다.\n로그창을 확인하세요."
                )
            )

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.run_btn.configure(state="normal"))


def main():
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()

    app = ImageReplaceApp(root)

    if not DND_AVAILABLE:
        app.log("알림: tkinterdnd2가 설치되지 않아 드래그앤드롭은 비활성화됩니다.")
        app.log("드래그앤드롭을 쓰려면 아래 명령어를 실행하세요.")
        app.log("pip install tkinterdnd2")

    root.mainloop()


if __name__ == "__main__":
    main()