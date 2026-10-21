class Buildq < Formula
  desc "Machine-wide FIFO build queue for parallel Xcode agents and worktrees"
  homepage "https://github.com/gruzd1sok/buildq"
  url "https://github.com/gruzd1sok/buildq/archive/refs/tags/v0.2.0.tar.gz"
  sha256 "950099feec10ed5bf7e1f78da349f173f96cbded8e3c926b573dabaa3eb50aa9"
  license "MIT"
  head "https://github.com/gruzd1sok/buildq.git", branch: "main"

  depends_on xcode: ["15.0", :build]
  depends_on :macos

  def install
    system "make", "app"
    bin.install "bin/buildq"
    prefix.install "build/BuildQBar.app"
  end

  service do
    run [opt_prefix/"BuildQBar.app/Contents/MacOS/BuildQBar"]
    environment_variables BUILDQ_CLI: opt_bin/"buildq"
    keep_alive successful_exit: false
    process_type :interactive
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/buildq --version")
    ENV["BUILDQ_HOME"] = (testpath/"bq").to_s
    assert_equal "ok\n", shell_output("#{bin}/buildq run -- echo ok")
  end
end
