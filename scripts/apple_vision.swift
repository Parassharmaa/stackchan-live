import CoreGraphics
import Foundation
import ImageIO
import Vision

struct VisionOutput: Codable {
    struct Label: Codable {
        let name: String
        let confidence: Float
    }

    let labels: [Label]
    let faceCount: Int
    let text: [String]
}

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: stackchan-vision IMAGE\n".utf8))
    exit(2)
}

let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard
    let source = CGImageSourceCreateWithURL(url as CFURL, nil),
    let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
else {
    FileHandle.standardError.write(Data("could not decode image\n".utf8))
    exit(3)
}

let classify = VNClassifyImageRequest()
let faces = VNDetectFaceRectanglesRequest()
let recognize = VNRecognizeTextRequest()
recognize.recognitionLevel = .fast
recognize.usesLanguageCorrection = true
recognize.recognitionLanguages = ["en-US", "ja-JP"]

do {
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([
        classify,
        faces,
        recognize,
    ])
} catch {
    FileHandle.standardError.write(Data("vision failed: \(error)\n".utf8))
    exit(4)
}

let labels = (classify.results ?? [])
    .filter { $0.confidence >= 0.03 }
    .prefix(5)
    .map { VisionOutput.Label(name: $0.identifier, confidence: $0.confidence) }
let recognizedText = (recognize.results ?? []).compactMap { observation in
    observation.topCandidates(1).first?.string
}.filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

let output = VisionOutput(
    labels: Array(labels),
    faceCount: faces.results?.count ?? 0,
    text: Array(recognizedText.prefix(8))
)
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
FileHandle.standardOutput.write(try encoder.encode(output))
FileHandle.standardOutput.write(Data("\n".utf8))
