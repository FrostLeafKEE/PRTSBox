use anyhow::{Result, bail};
use std::{
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

#[derive(Clone, Default)]
pub struct CancelToken {
    pub epoch: Arc<AtomicU64>,
    pub expected: u64,
}
impl CancelToken {
    pub fn check(&self) -> Result<()> {
        if self.epoch.load(Ordering::Acquire) != self.expected {
            bail!("任务已取消");
        }
        Ok(())
    }
    pub fn sleep(&self, duration: Duration) -> Result<()> {
        let deadline = Instant::now() + duration;
        loop {
            self.check()?;
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Ok(());
            }
            std::thread::sleep(remaining.min(Duration::from_millis(25)));
        }
    }
}
