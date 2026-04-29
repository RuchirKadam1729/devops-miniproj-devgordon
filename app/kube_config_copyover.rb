require 'fileutils'
require 'yaml'

home = ENV["HOME"]
FileUtils.mkdir_p("#{home}/.kube")

config = YAML.load_file("/.kube/config")

config["clusters"].each do |cluster|
  server = cluster["cluster"]["server"]
  next unless server.include?("127.0.0.1") || server.include?("localhost")

  cluster["cluster"]["server"] = server.gsub("127.0.0.1", "host.docker.internal").gsub("localhost", "host.docker.internal")
  cluster["cluster"].delete("certificate-authority-data")
  cluster["cluster"]["insecure-skip-tls-verify"] = true
end

File.write("#{home}/.kube/config", config.to_yaml)