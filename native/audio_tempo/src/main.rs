use std::ffi::OsString;
use std::path::PathBuf;
use syncfl_tempo::{analyze, Error, Options, VERSION};

const USAGE: &str = "Usage: syncfl-tempo analyze --input <file.wav> [--min-bpm 40] [--max-bpm 240] [--top-n 5]\n       syncfl-tempo --version";

fn parse(args: impl IntoIterator<Item = OsString>) -> Result<(PathBuf, Options), Error> {
    let mut args = args.into_iter();
    if args.next().as_deref() != Some(std::ffi::OsStr::new("analyze")) {
        return Err(Error::new("invalid_arguments", USAGE));
    }
    let mut path = None;
    let mut options = Options::default();
    let mut seen = [false; 4];
    while let Some(argument) = args.next() {
        let name = argument
            .to_str()
            .ok_or_else(|| Error::new("invalid_arguments", "Option name must be valid UTF-8"))?;
        let index = match name {
            "--input" => 0,
            "--min-bpm" => 1,
            "--max-bpm" => 2,
            "--top-n" => 3,
            _ => return Err(Error::new("invalid_arguments", "Unknown option")),
        };
        if seen[index] {
            return Err(Error::new("invalid_arguments", "Duplicate option"));
        }
        seen[index] = true;
        let value = args
            .next()
            .ok_or_else(|| Error::new("invalid_arguments", "Missing option value"))?;
        if index == 0 {
            path = Some(PathBuf::from(value));
            continue;
        }
        let value = value
            .to_str()
            .ok_or_else(|| Error::new("invalid_arguments", "Numeric option must be valid UTF-8"))?;
        match index {
            1 => {
                options.min_bpm = value
                    .parse()
                    .map_err(|_| Error::new("invalid_arguments", "Invalid minimum BPM"))?
            }
            2 => {
                options.max_bpm = value
                    .parse()
                    .map_err(|_| Error::new("invalid_arguments", "Invalid maximum BPM"))?
            }
            3 => {
                options.top_n = value
                    .parse()
                    .map_err(|_| Error::new("invalid_arguments", "Invalid candidate count"))?
            }
            _ => unreachable!(),
        }
    }
    options.validate()?;
    Ok((
        path.ok_or_else(|| Error::new("invalid_arguments", "--input is required"))?,
        options,
    ))
}

fn main() {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    if arguments.len() == 1 && arguments[0] == "--version" {
        println!("syncfl-tempo {VERSION}");
        return;
    }
    if arguments.len() == 1 && (arguments[0] == "--help" || arguments[0] == "-h") {
        println!("{USAGE}");
        return;
    }
    match parse(arguments).and_then(|(path, options)| analyze(path, options)) {
        Ok(analysis) => println!("{}", analysis.to_json()),
        Err(error) => {
            eprintln!("{}", error.to_json());
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn validates_cli_before_opening_input() {
        assert!(parse(args(&["analyze", "--input", "a.wav", "--min-bpm", "NaN"])).is_err());
        assert!(parse(args(&["analyze", "--input", "a.wav", "--input", "b.wav"])).is_err());
        assert!(parse(args(&["analyze", "--input", "a.wav", "--top-n", "11"])).is_err());
        assert!(parse(args(&["analyze", "--input"])).is_err());
        assert!(parse(args(&["analyze", "--unknown", "a.wav"])).is_err());
        assert!(parse(args(&["analyze", "--input", "a.wav"])).is_ok());
    }
}
