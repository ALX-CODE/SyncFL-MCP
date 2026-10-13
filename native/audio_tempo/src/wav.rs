//! Small, bounded RIFF reader. The public format description is Microsoft's
//! WAVEFORMATEXTENSIBLE documentation, not a third-party parser implementation:
//! https://learn.microsoft.com/windows-hardware/drivers/ddi/ksmedia/ns-ksmedia-waveformatextensible

use crate::Error;
use std::fs::{File, Metadata};
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

const MAX_CHUNKS: usize = 65_536;
const GUID_SUFFIX: [u8; 12] = [0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113];

#[derive(Debug, Clone, Copy)]
pub(crate) struct Header {
    pub channels: u16,
    pub sample_rate: u32,
    pub block_align: u16,
    pub data_bytes: u64,
    bits: u16,
    float: bool,
}

pub(crate) struct Reader {
    file: File,
    metadata: Metadata,
    remaining: u64,
    pub header: Header,
}

fn read_exact(file: &mut File, bytes: &mut [u8]) -> Result<(), Error> {
    file.read_exact(bytes).map_err(|error| {
        if error.kind() == std::io::ErrorKind::UnexpectedEof {
            Error::new("invalid_wav", "WAV data or header is truncated")
        } else {
            Error::new("io_error", format!("Could not read WAV ({})", error.kind()))
        }
    })
}

fn seek(file: &mut File, offset: u64) -> Result<(), Error> {
    file.seek(SeekFrom::Start(offset))
        .map(|_| ())
        .map_err(|error| Error::new("io_error", format!("Could not seek WAV ({})", error.kind())))
}

fn u16_at(bytes: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([bytes[offset], bytes[offset + 1]])
}

fn u32_at(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        bytes[offset],
        bytes[offset + 1],
        bytes[offset + 2],
        bytes[offset + 3],
    ])
}

impl Reader {
    pub fn open(path: &Path) -> Result<Self, Error> {
        let mut file = File::open(path).map_err(|error| {
            Error::new("io_error", format!("Could not open WAV ({})", error.kind()))
        })?;
        let metadata = file
            .metadata()
            .map_err(|_| Error::new("io_error", "Could not read WAV metadata"))?;
        if !metadata.is_file() {
            return Err(Error::new(
                "invalid_wav",
                "Input must be a regular WAV file",
            ));
        }
        let mut riff = [0_u8; 12];
        read_exact(&mut file, &mut riff)?;
        if &riff[0..4] != b"RIFF" || &riff[8..12] != b"WAVE" {
            return Err(Error::new(
                "unsupported_wav",
                "Require little-endian RIFF WAVE; RF64 and RIFX are unsupported",
            ));
        }
        let riff_end = u64::from(u32_at(&riff, 4)) + 8;
        if riff_end < 12 || riff_end > metadata.len() {
            return Err(Error::new(
                "invalid_wav",
                "RIFF length is invalid or truncated",
            ));
        }
        let mut offset = 12_u64;
        let mut format = None;
        let mut data = None;
        let mut chunk_count = 0;
        while offset < riff_end {
            chunk_count += 1;
            if chunk_count > MAX_CHUNKS {
                return Err(Error::new("invalid_wav", "Too many RIFF chunks"));
            }
            if riff_end - offset < 8 {
                return Err(Error::new("invalid_wav", "Incomplete RIFF chunk header"));
            }
            seek(&mut file, offset)?;
            let mut chunk = [0_u8; 8];
            read_exact(&mut file, &mut chunk)?;
            let length = u64::from(u32_at(&chunk, 4));
            let payload = offset + 8;
            let end = payload + length;
            let padded_end = end + (length & 1);
            if padded_end > riff_end {
                return Err(Error::new(
                    "invalid_wav",
                    "RIFF chunk exceeds its container",
                ));
            }
            match &chunk[0..4] {
                b"fmt " => {
                    if format.is_some() {
                        return Err(Error::new("invalid_wav", "Duplicate WAV format chunk"));
                    }
                    if !(16..=65_536).contains(&length) {
                        return Err(Error::new("invalid_wav", "Invalid WAV format chunk size"));
                    }
                    let mut bytes = [0_u8; 40];
                    let count = length.min(40) as usize;
                    read_exact(&mut file, &mut bytes[..count])?;
                    format = Some(parse_format(&bytes, length)?);
                }
                b"data" => {
                    if data.is_some() {
                        return Err(Error::new(
                            "invalid_wav",
                            "Multiple WAV data chunks are unsupported",
                        ));
                    }
                    data = Some((payload, length));
                }
                _ => {}
            }
            offset = padded_end;
        }
        let mut header =
            format.ok_or_else(|| Error::new("invalid_wav", "Missing WAV format chunk"))?;
        let (data_offset, data_bytes) =
            data.ok_or_else(|| Error::new("invalid_wav", "Missing WAV data chunk"))?;
        if data_bytes % u64::from(header.block_align) != 0 {
            return Err(Error::new(
                "invalid_wav",
                "WAV data does not contain complete sample frames",
            ));
        }
        header.data_bytes = data_bytes;
        seek(&mut file, data_offset)?;
        Ok(Self {
            file,
            metadata,
            remaining: data_bytes,
            header,
        })
    }

    /// Fills an interleaved buffer with a bounded number of complete frames.
    pub fn read_frames(
        &mut self,
        max_frames: usize,
        output: &mut Vec<f64>,
    ) -> Result<usize, Error> {
        let max_frames = max_frames.min(4096);
        let available = self.remaining / u64::from(self.header.block_align);
        let frames = available.min(max_frames as u64) as usize;
        let count = frames * usize::from(self.header.block_align);
        let mut bytes = vec![0_u8; count];
        read_exact(&mut self.file, &mut bytes)?;
        self.remaining -= count as u64;
        output.clear();
        output.reserve(frames * usize::from(self.header.channels));
        let width = usize::from(self.header.bits / 8);
        for sample in bytes.chunks_exact(width) {
            let value = match (self.header.float, self.header.bits) {
                (false, 8) => (f64::from(sample[0]) - 128.0) / 128.0,
                (false, 16) => f64::from(i16::from_le_bytes([sample[0], sample[1]])) / 32768.0,
                (false, 24) => {
                    let value = (i32::from(sample[0])
                        | i32::from(sample[1]) << 8
                        | i32::from(sample[2]) << 16)
                        << 8
                        >> 8;
                    f64::from(value) / 8_388_608.0
                }
                (false, 32) => {
                    f64::from(i32::from_le_bytes([
                        sample[0], sample[1], sample[2], sample[3],
                    ])) / 2_147_483_648.0
                }
                (true, 32) => f64::from(f32::from_le_bytes([
                    sample[0], sample[1], sample[2], sample[3],
                ])),
                (true, 64) => f64::from_le_bytes([
                    sample[0], sample[1], sample[2], sample[3], sample[4], sample[5], sample[6],
                    sample[7],
                ]),
                _ => return Err(Error::new("unsupported_wav", "Unsupported sample encoding")),
            };
            if !value.is_finite() || value.abs() > 1e100 {
                return Err(Error::new(
                    "invalid_samples",
                    "Samples must be finite and have magnitude <= 1e100",
                ));
            }
            output.push(value);
        }
        Ok(frames)
    }

    pub fn verify_unchanged(&self) -> Result<(), Error> {
        let current = self
            .file
            .metadata()
            .map_err(|_| Error::new("io_error", "Could not recheck WAV metadata"))?;
        if self.metadata.len() != current.len()
            || self.metadata.modified().ok() != current.modified().ok()
        {
            return Err(Error::new(
                "input_changed",
                "WAV changed while it was being analyzed",
            ));
        }
        Ok(())
    }
}

fn parse_format(bytes: &[u8; 40], length: u64) -> Result<Header, Error> {
    let mut tag = u16_at(bytes, 0);
    let channels = u16_at(bytes, 2);
    let sample_rate = u32_at(bytes, 4);
    let byte_rate = u32_at(bytes, 8);
    let block_align = u16_at(bytes, 12);
    let bits = u16_at(bytes, 14);
    if tag == 0xfffe {
        if length < 40 || u16_at(bytes, 16) < 22 || u64::from(u16_at(bytes, 16)) + 18 > length {
            return Err(Error::new(
                "invalid_wav",
                "Incomplete WAVE_FORMAT_EXTENSIBLE header",
            ));
        }
        let valid_bits = u16_at(bytes, 18);
        if valid_bits == 0 || valid_bits > bits || bytes[28..40] != GUID_SUFFIX {
            return Err(Error::new(
                "unsupported_wav",
                "Invalid or unsupported WAV extensible subformat",
            ));
        }
        let subformat = u32_at(bytes, 24);
        if subformat != 1 && subformat != 3 {
            return Err(Error::new(
                "unsupported_wav",
                "Require PCM or IEEE float WAV",
            ));
        }
        tag = subformat as u16;
        if tag == 3 && valid_bits != bits {
            return Err(Error::new(
                "invalid_wav",
                "Float WAV valid bits must match its container",
            ));
        }
    } else if length == 17 || (length >= 18 && u64::from(u16_at(bytes, 16)) + 18 > length) {
        return Err(Error::new("invalid_wav", "Invalid WAV extension length"));
    }
    let float = match tag {
        1 if [8, 16, 24, 32].contains(&bits) => false,
        3 if [32, 64].contains(&bits) => true,
        _ => {
            return Err(Error::new(
                "unsupported_wav",
                "Require PCM 8/16/24/32-bit or IEEE float 32/64-bit WAV",
            ))
        }
    };
    if !(1..=8).contains(&channels) || !(4000..=384_000).contains(&sample_rate) {
        return Err(Error::new(
            "unsupported_wav",
            "Require 1..8 channels and sample rate 4000..384000 Hz",
        ));
    }
    let expected_align = channels * (bits / 8);
    if block_align != expected_align || byte_rate != sample_rate * u32::from(expected_align) {
        return Err(Error::new(
            "invalid_wav",
            "Inconsistent WAV block alignment or byte rate",
        ));
    }
    Ok(Header {
        channels,
        sample_rate,
        block_align,
        data_bytes: 0,
        bits,
        float,
    })
}
