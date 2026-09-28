use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Command,
};

fn main() {
    println!("cargo:rerun-if-changed=assets/app.ico");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let Some(rc) = resource_compiler() else {
        println!("cargo:warning=Windows resource compiler not found; executable icon omitted");
        return;
    };
    let out = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR"));
    let icon = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"))
        .join("assets/app.ico");
    let script = out.join("prtsbox.rc");
    let resource = out.join("prtsbox.res");
    fs::write(
        &script,
        format!(
            "1 ICON \"{}\"\n",
            icon.display().to_string().replace('\\', "/")
        ),
    )
    .expect("write Windows resource script");
    let result = Command::new(rc)
        .arg("/nologo")
        .arg(format!("/fo{}", resource.display()))
        .arg(&script)
        .status()
        .expect("run Windows resource compiler");
    assert!(result.success(), "Windows resource compilation failed");
    println!("cargo:rustc-link-arg={}", resource.display());
}

fn resource_compiler() -> Option<PathBuf> {
    if let Some(value) = env::var_os("RC") {
        return Some(PathBuf::from(value));
    }
    let root = Path::new(&env::var_os("ProgramFiles(x86)")?).join("Windows Kits/10/bin");
    let mut candidates = fs::read_dir(root)
        .ok()?
        .filter_map(Result::ok)
        .map(|item| item.path().join("x64/rc.exe"))
        .filter(|path| path.is_file())
        .collect::<Vec<_>>();
    candidates.sort();
    candidates.pop()
}
